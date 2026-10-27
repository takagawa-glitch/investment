# -*- coding: utf-8 -*-
"""画面(tkinter)。取得は別スレッドで行い、画面が固まらないようにする。"""

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from . import codes, edinet, records, report, service
from .paths import APP_NAME
from .startup_error import show_runtime_error, write_error_log

FONT = ("Meiryo UI", 10)


class App:
    def __init__(self, root):
        self.root = root
        self.queue = queue.Queue()
        self.cancel = threading.Event()
        self.worker = None
        self.results = {}       # 表の行ID → StockResult

        root.title(f"{APP_NAME}(株価・PSR・自己資本比率・営業CF・有利子負債)")
        root.geometry("1180x680")
        root.minsize(900, 520)
        root.report_callback_exception = self._on_tk_error
        style = ttk.Style(root)
        style.configure(".", font=FONT)
        style.configure("Treeview", rowheight=24)
        style.configure("Treeview.Heading", font=("Meiryo UI", 9, "bold"))

        top = ttk.Frame(root, padding=(10, 10, 10, 4))
        top.pack(fill="x")
        ttk.Label(top, text="銘柄コード(例: 554A)または銘柄名を入力してください。"
                            "複数のときは改行・カンマ・空白で区切ります。").pack(anchor="w")
        row = ttk.Frame(top)
        row.pack(fill="x", pady=(4, 0))
        self.entry = tk.Text(row, height=3, font=("Meiryo UI", 11), wrap="word")
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.insert("1.0", "554A")
        self.entry.bind("<Control-Return>", lambda e: (self.start(), "break")[1])
        btns = ttk.Frame(row)
        btns.pack(side="left", padx=(8, 0), fill="y")
        self.btn_start = ttk.Button(btns, text="情報を取得する", command=self.start)
        self.btn_start.pack(fill="x", ipady=4)
        self.btn_stop = ttk.Button(btns, text="中止", command=self.stop, state="disabled")
        self.btn_stop.pack(fill="x", pady=(4, 0))

        bar = ttk.Frame(root, padding=(10, 0, 10, 4))
        bar.pack(fill="x")
        ttk.Button(bar, text="EDINETのAPIキーを設定", command=self.set_key).pack(side="left")
        self.key_label = ttk.Label(bar, text="")
        self.key_label.pack(side="left", padx=8)
        ttk.Button(bar, text="表を空にする", command=self.clear).pack(side="right")
        self._update_key_label()

        mid = ttk.Frame(root, padding=(10, 0, 10, 0))
        mid.pack(fill="both", expand=True)
        cols = [c[0] for c in report.COLUMNS]
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="browse")
        for name, head, width, right in report.COLUMNS:
            self.tree.heading(name, text=head)
            self.tree.column(name, width=width, anchor="e" if right else "w", stretch=False)
        self.tree.column("status", stretch=True)
        self.tree.tag_configure("要確認", background="#fff4cc")
        self.tree.tag_configure("失敗", background="#fde2e2")
        self.tree.tag_configure("一部取得できず", background="#fff4cc")
        ys = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        xs = ttk.Scrollbar(mid, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        mid.rowconfigure(0, weight=1)
        mid.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        ttk.Label(root, text="選んだ銘柄のくわしい内容(出典・計算方法・注意)",
                  padding=(10, 6, 10, 0)).pack(anchor="w")
        self.detail = tk.Text(root, height=11, font=("Meiryo UI", 10), wrap="word",
                              state="disabled", background="#f7f7f7")
        self.detail.pack(fill="x", padx=10)

        self.status = ttk.Label(root, text="準備ができました", padding=(10, 4), anchor="w")
        self.status.pack(fill="x")

        records.cleanup_old_logs()
        root.after(100, self._poll)

    # ---------------------------------------------------------------- 操作

    def _update_key_label(self):
        if edinet.load_api_key():
            self.key_label.configure(text="APIキー: 設定済み(有利子負債は EDINET の正確な値)")
        else:
            self.key_label.configure(text="有利子負債は株探の倍率から概算します(倍率が無い会社は「－」)")

    def set_key(self):
        win = tk.Toplevel(self.root)
        win.title("EDINETのAPIキーを設定")
        win.transient(self.root)
        win.grab_set()
        f = ttk.Frame(win, padding=14)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="金融庁 EDINET のサイトで取得した APIキー(Subscription-Key)を貼り付けてください。\n"
                          "キーはこのパソコンの中だけに保存し、Box には置きません。",
                  justify="left").pack(anchor="w")
        var = tk.StringVar(value=edinet.load_api_key() or "")
        ent = ttk.Entry(f, textvariable=var, width=60, show="*")
        ent.pack(fill="x", pady=8)
        ent.focus_set()

        def save():
            key = var.get().strip()
            if not key:
                messagebox.showwarning("APIキー", "APIキーが空です。", parent=win)
                return
            try:
                edinet.save_api_key(key)
            except OSError as e:
                messagebox.showerror("APIキー", f"保存できませんでした。\n{e}", parent=win)
                return
            records.write_record("EDINETのAPIキー設定", "", "成功", "")
            self._update_key_label()
            win.destroy()

        b = ttk.Frame(f)
        b.pack(fill="x")
        ttk.Button(b, text="保存する", command=save).pack(side="right")
        ttk.Button(b, text="やめる", command=win.destroy).pack(side="right", padx=6)

    def clear(self):
        if self.worker and self.worker.is_alive():
            return
        self.tree.delete(*self.tree.get_children())
        self.results.clear()
        self._show_detail("")

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        queries = codes.split_queries(self.entry.get("1.0", "end"))
        if not queries:
            messagebox.showinfo(APP_NAME, "銘柄コードまたは銘柄名を入力してください。")
            return
        self.cancel.clear()
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.worker = threading.Thread(target=self._work, args=(queries,), daemon=True)
        self.worker.start()

    def stop(self):
        self.cancel.set()
        self.status.configure(text="中止しています…(取得中の1件が終わりしだい止まります)")

    # ---------------------------------------------------------------- 取得(別スレッド)

    def _work(self, queries):
        post = self.queue.put
        try:
            post(("status", "銘柄の一覧を準備しています"))
            book, note = codes.load_codebook()
            if note:
                post(("status", note))
            key = edinet.load_api_key()
            done = 0
            for q in queries:
                if self.cancel.is_set():
                    break
                try:
                    r = service.get_stock(q, book, key,
                                          progress=lambda s: post(("status", s)),
                                          cancelled=self.cancel.is_set)
                except edinet.Cancelled:
                    break
                except Exception as e:
                    logged = write_error_log(e, f"取得中のエラー({q})")
                    r = service.StockResult(query=q, status="失敗", notes=[
                        f"予期しないエラーで取得できませんでした({type(e).__name__})"
                        + (f"。記録: {logged}" if logged else "")])
                if note:
                    r.notes.append(note)
                post(("result", r))
                records.write_record(
                    "銘柄情報の取得", f"{r.code or q} {r.name}".strip(), r.status,
                    " / ".join(r.notes))
                done += 1
            msg = f"{done}件の取得が終わりました"
            if self.cancel.is_set():
                msg = f"中止しました({done}件取得済み)"
            records.write_record("銘柄情報の取得(全体)", f"{len(queries)}件",
                                 "中止" if self.cancel.is_set() else "完了", msg)
            post(("done", msg))
        except Exception as e:
            post(("error", e))

    # ---------------------------------------------------------------- 画面の更新

    def _poll(self):
        try:
            while True:
                kind, val = self.queue.get_nowait()
                if kind == "status":
                    self.status.configure(text=val)
                elif kind == "result":
                    iid = self.tree.insert("", "end", values=report.row_values(val),
                                           tags=(val.status,))
                    self.results[iid] = val
                    self.tree.selection_set(iid)
                    self.tree.see(iid)
                elif kind in ("done", "error"):
                    self.btn_start.configure(state="normal")
                    self.btn_stop.configure(state="disabled")
                    if kind == "done":
                        self.status.configure(text=val)
                    else:
                        self.status.configure(text="エラーで止まりました")
                        show_runtime_error(val)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _on_select(self, _event=None):
        sel = self.tree.selection()
        if sel and sel[0] in self.results:
            self._show_detail(report.detail_text(self.results[sel[0]]))

    def _show_detail(self, text):
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")

    def _on_tk_error(self, exc_type, exc, tb):
        exc.__traceback__ = tb
        show_runtime_error(exc)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()

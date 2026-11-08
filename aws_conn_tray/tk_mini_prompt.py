"""
tkinter で右下に表示するミニダイアログ。
サブプロセスとして起動し、ユーザー操作（開く）を signal_file に書き込む。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import tkinter as tk
from typing import Optional
from urllib.parse import urlparse, parse_qs


def _extract_user_code_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    parsed = urlparse(url)
    query = parsed.query or ""
    frag = parsed.fragment or ""
    for blob in (query, frag):
        if not blob:
            continue
        if "?" in blob:
            blob = blob.split("?", 1)[1]
        qs = parse_qs(blob)
        code = (qs.get("user_code") or [None])[0]
        if code:
            return code
    return None


def _write_signal(path: Optional[str], value: str) -> None:
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(value)
    except Exception:
        pass


def _copy_to_clipboard(root: tk.Tk, text: str) -> None:
    try:
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update_idletasks()
    except Exception:
        pass


def show_prompt(
    message: str,
    button_label: str = "開く",
    user_code: Optional[str] = None,
    signal_file: Optional[str] = None,
    show_button: bool = True,
) -> None:
    root = tk.Tk()
    root.title("")
    # タイトルバーを消し、タスクバーに出さない
    try:
        root.overrideredirect(True)
    except Exception:
        pass
    try:
        root.wm_attributes("-topmost", True)
    except Exception:
        pass
    try:
        root.wm_attributes("-toolwindow", True)
    except Exception:
        pass
    try:
        root.wm_attributes("-alpha", 0.9)
    except Exception:
        pass

    bg = "#f5f5f5"
    fg = "#000000"
    root.configure(bg=bg)

    frame = tk.Frame(root, bg=bg, padx=12, pady=12)
    frame.pack(fill="both", expand=True)

    label = tk.Label(
        frame,
        text=message,
        justify="left",
        wraplength=260,
        bg=bg,
        fg=fg,
        font=("TkDefaultFont", 12, "bold"),
    )
    label.pack(anchor="w")

    if user_code:
        code_frame = tk.Frame(frame, bg=bg)
        code_frame.pack(anchor="w", pady=(8, 0), fill="x")
        code_label = tk.Label(
            code_frame,
            text="認証コード:",
            bg=bg,
            fg=fg,
            font=("TkDefaultFont", 12, "bold"),
        )
        code_label.pack(side="left")
        code_value = tk.Label(
            code_frame,
            text=user_code,
            bg=bg,
            fg=fg,
            font=("TkDefaultFont", 12, "bold"),
        )
        code_value.pack(side="left", padx=(6, 8))

        def on_copy():
            _copy_to_clipboard(root, user_code)

        copy_btn = tk.Button(code_frame, text="コピー", command=on_copy)
        copy_btn.pack(side="left")

    if show_button and button_label:
        button_row = tk.Frame(frame, bg=bg)
        button_row.pack(anchor="e", pady=(10, 0), fill="x")

        def on_open():
            _write_signal(signal_file, "open")
            root.destroy()

        open_btn = tk.Button(button_row, text=button_label, command=on_open)
        open_btn.pack(side="right")

    def on_close():
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

    root.update_idletasks()
    w = max(320, root.winfo_reqwidth())
    h = max(140, root.winfo_reqheight())
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    x = max(0, sw - w - 20)
    y = max(0, sh - h - 40)
    root.geometry(f"{w}x{h}+{x}+{y}")

    # フォーカスを極力奪わないようにする
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass

    root.mainloop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--message", required=True)
    parser.add_argument("--button-label", default="開く")
    parser.add_argument("--no-button", action="store_true")
    parser.add_argument("--user-code", default=None)
    parser.add_argument("--auth-url", default=None)
    parser.add_argument("--signal-file", default=None)
    args = parser.parse_args()

    user_code = args.user_code
    if not user_code and args.auth_url:
        user_code = _extract_user_code_from_url(args.auth_url)

    show_prompt(
        message=args.message,
        button_label=args.button_label,
        user_code=user_code,
        signal_file=args.signal_file,
        show_button=not args.no_button,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

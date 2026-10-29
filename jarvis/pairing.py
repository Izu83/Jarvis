"""The "Pair your phone" window (tray menu > Pair your phone): a QR code and the pairing link, so the phone can be
connected without a console. Runs as its own small process, like the pop-up, so it never holds up Jarvis.

    pythonw -m jarvis.pairing
"""
import ctypes
import sys
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.windows import enable_dpi_awareness  # noqa: E402

TITLE = "Pair your phone with Jarvis"
BG, CARD, TEXT, SUB, BLUE, LINE = "#0b0d12", "#161a22", "#f2f4f8", "#9aa3b2", "#3b82f6", "#262c38"


def already_open() -> bool:
    """Bring an open pairing window to the front instead of opening a second one."""
    hwnd = ctypes.windll.user32.FindWindowW(None, TITLE)
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 9)   # SW_RESTORE
        ctypes.windll.user32.SetForegroundWindow(hwnd)
    return bool(hwnd)


class PairWindow:
    def __init__(self, link: str, missing_page: bool):
        import qrcode
        from PIL import ImageTk

        self.link = link
        self.root = root = tk.Tk()
        root.title(TITLE)
        root.configure(bg=BG)
        root.resizable(False, False)
        scale = root.winfo_fpixels("1i") / 96   # the window's pixels per 96 dpi, for the QR image
        icon = Path(__file__).resolve().parent.parent / "docs" / "icon-192.png"
        if icon.exists():
            try:
                self._icon = tk.PhotoImage(file=str(icon))
                root.iconphoto(True, self._icon)
            except tk.TclError:
                pass

        big = tkfont.Font(family="Segoe UI Semibold", size=17)
        body = tkfont.Font(family="Segoe UI", size=10)
        small = tkfont.Font(family="Segoe UI", size=9)
        pad = int(22 * scale)

        tk.Label(root, text="Pair your phone", font=big, fg=TEXT, bg=BG).pack(anchor="w", padx=pad, pady=(pad, 2))
        tk.Label(root, text="Open the Camera on your iPhone and point it at this code, or open the link on the phone.\n"
                            "Then tap Share → Add to Home Screen.",
                 font=body, fg=SUB, bg=BG, justify="left").pack(anchor="w", padx=pad)

        qr = qrcode.QRCode(border=2, box_size=10, error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(link)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").get_image().convert("RGB")
        side = int(300 * scale)
        self._qr = ImageTk.PhotoImage(img.resize((side, side), 0))   # nearest neighbour keeps the squares sharp
        tk.Label(root, image=self._qr, bg="white", bd=0).pack(padx=pad, pady=int(16 * scale))

        tk.Label(root, text="Pairing link", font=small, fg=SUB, bg=BG).pack(anchor="w", padx=pad)
        box = tk.Text(root, height=4, width=52, wrap="char", font=small, fg=TEXT, bg=CARD, bd=0,
                      highlightthickness=1, highlightbackground=LINE, highlightcolor=LINE, padx=8, pady=6)
        box.insert("1.0", link)
        box.configure(state="disabled")   # read-only, but still selectable
        box.pack(fill="x", padx=pad, pady=(4, 0))

        warn = ("This link holds your private key. Don't share it or post it anywhere.")
        if missing_page:
            warn = ("phone_page_url isn't set in config.json yet: set it to your GitHub Pages address, then open "
                    "this window again. " + warn)
        tk.Label(root, text=warn, font=small, fg="#f5a524" if missing_page else SUB, bg=BG, justify="left",
                 wraplength=int(440 * scale)).pack(anchor="w", padx=pad, pady=(8, 0))

        row = tk.Frame(root, bg=BG)
        row.pack(fill="x", padx=pad, pady=(int(16 * scale), pad))
        self.copy_btn = self._button(row, "Copy link", self.copy, primary=True)
        self.copy_btn.pack(side="left")
        self._button(row, "Close", root.destroy).pack(side="right")
        root.bind("<Escape>", lambda e: root.destroy())

        root.update_idletasks()   # centre it on the screen it opens on
        w, h = root.winfo_reqwidth(), root.winfo_reqheight()
        root.geometry(f"+{(root.winfo_screenwidth() - w) // 2}+{max(0, (root.winfo_screenheight() - h) // 2)}")
        root.attributes("-topmost", True)
        root.after(400, lambda: root.attributes("-topmost", False))   # come to the front once, then behave normally
        root.focus_force()

    @staticmethod
    def _button(parent, text, command, primary=False):
        font = tkfont.Font(family="Segoe UI Semibold", size=10)
        return tk.Button(parent, text=text, command=command, font=font, cursor="hand2", bd=0, padx=16, pady=7,
                         fg="white" if primary else TEXT, bg=BLUE if primary else CARD,
                         activeforeground="white", activebackground="#2563eb" if primary else LINE, relief="flat")

    def copy(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.link)
        self.root.update()   # keep it on the clipboard after the window closes
        self.copy_btn.configure(text="Copied ✓")
        self.root.after(1800, lambda: self.copy_btn.configure(text="Copy link"))

    def run(self):
        self.root.mainloop()


def main() -> None:
    enable_dpi_awareness()
    if already_open():
        return
    from jarvis.config import settings
    from jarvis.remote import pairing_link
    PairWindow(pairing_link(), not settings.get("phone_page_url")).run()


if __name__ == "__main__":
    main()

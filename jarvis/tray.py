"""A system-tray icon (the little arrow in the Windows taskbar) so Jarvis can run with no console window.

Right-click the icon for a menu: open the phone page, open the log, or quit. The icon lives on its own thread
with a tiny Windows message loop; quitting from it stops the whole process. No extra dependency — this uses
pywin32, which Jarvis already needs."""
import logging
import os
import webbrowser
from pathlib import Path

import win32api
import win32con
import win32gui

from .config import ROOT, settings

log = logging.getLogger("jarvis.tray")

WM_TRAY = win32con.WM_USER + 20   # the message Windows sends us when the icon is clicked
ID_PHONE, ID_LOG, ID_QUIT = 1023, 1024, 1025


def _ensure_ico() -> Path | None:
    """Windows tray icons must be .ico; build one once from the app's PNG (cached in .brain)."""
    cache = ROOT / ".brain" / "tray.ico"
    if cache.exists():
        return cache
    src = ROOT / "docs" / "icon-192.png"
    if not src.exists():
        return None
    try:
        from PIL import Image
        cache.parent.mkdir(parents=True, exist_ok=True)
        Image.open(src).convert("RGBA").save(cache, sizes=[(16, 16), (24, 24), (32, 32), (48, 48)])
        return cache
    except Exception:
        log.debug("couldn't build tray.ico", exc_info=True)
        return None


class Tray:
    def __init__(self):
        self.hwnd = None

    def _load_icon(self):
        ico = _ensure_ico()
        if ico:
            try:
                w = win32api.GetSystemMetrics(win32con.SM_CXSMICON)
                h = win32api.GetSystemMetrics(win32con.SM_CYSMICON)
                return win32gui.LoadImage(0, str(ico), win32con.IMAGE_ICON, w, h, win32con.LR_LOADFROMFILE)
            except Exception:
                log.debug("couldn't load tray.ico, using the default icon", exc_info=True)
        return win32gui.LoadIcon(0, win32con.IDI_APPLICATION)

    def run(self) -> None:
        """Create the icon and pump its messages. Blocks until the process quits, so run it on its own thread."""
        wc = win32gui.WNDCLASS()
        wc.hInstance = win32gui.GetModuleHandle(None)
        wc.lpszClassName = "JarvisTray"
        wc.lpfnWndProc = {WM_TRAY: self._on_tray, win32con.WM_COMMAND: self._on_command}
        atom = win32gui.RegisterClass(wc)
        self.hwnd = win32gui.CreateWindow(atom, "Jarvis", win32con.WS_OVERLAPPED, 0, 0, 0, 0, 0, 0, wc.hInstance, None)
        nid = (self.hwnd, 0, win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP,
               WM_TRAY, self._load_icon(), "Jarvis — listening")
        win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, nid)
        log.info("Tray icon added (find it under the ^ arrow in the taskbar).")
        win32gui.PumpMessages()

    def _on_tray(self, hwnd, msg, wparam, lparam):
        if lparam in (win32con.WM_RBUTTONUP, win32con.WM_LBUTTONUP):
            menu = win32gui.CreatePopupMenu()
            win32gui.AppendMenu(menu, win32con.MF_STRING, ID_PHONE, "Open the phone page")
            win32gui.AppendMenu(menu, win32con.MF_STRING, ID_LOG, "Open the log")
            win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
            win32gui.AppendMenu(menu, win32con.MF_STRING, ID_QUIT, "Quit Jarvis")
            x, y = win32gui.GetCursorPos()
            win32gui.SetForegroundWindow(hwnd)   # so clicking elsewhere dismisses the menu
            win32gui.TrackPopupMenu(menu, win32con.TPM_LEFTALIGN, x, y, 0, hwnd, None)
            win32gui.PostMessage(hwnd, win32con.WM_NULL, 0, 0)
        return True

    def _on_command(self, hwnd, msg, wparam, lparam):
        cmd = wparam & 0xFFFF
        if cmd == ID_PHONE:
            url = settings["phone_page_url"]
            if url:
                webbrowser.open(url)
        elif cmd == ID_LOG:
            try:
                os.startfile(str(ROOT / "jarvis.log"))
            except Exception:
                log.debug("couldn't open the log", exc_info=True)
        elif cmd == ID_QUIT:
            log.info("Quit from the tray icon.")
            try:
                win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (hwnd, 0))
            except Exception:
                pass
            os._exit(0)   # end the whole process, including the daemon voice/phone threads
        return True

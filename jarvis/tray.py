"""A system-tray icon (the little arrow in the Windows taskbar) so Jarvis can run with no console window.

Right-click the icon for a menu: pick the microphone, toggle whether Jarvis speaks when you use the Ctrl+Alt+J
shortcut, pair your phone (QR code + link), open the phone page or the log, restart or quit. The icon lives on its own thread with a tiny Windows message
loop; quitting from it stops the whole process. No extra dependency — this uses pywin32, which Jarvis already needs.
"""
import logging
import os
import subprocess
import sys
import webbrowser
from pathlib import Path

import win32api
import win32con
import win32gui

from . import voice
from .config import ROOT, set_setting, settings

log = logging.getLogger("jarvis.tray")

WM_TRAY = win32con.WM_USER + 20   # the message Windows sends us when the icon is clicked
ID_PHONE, ID_LOG, ID_QUIT, ID_SPEAK, ID_RESTART, ID_PAIR = 1023, 1024, 1025, 1026, 1027, 1028
MIC_BASE = 1100                   # microphone items get ids MIC_BASE, MIC_BASE+1, ...


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
    def __init__(self, listener=None, on_quit=None):
        self.hwnd = None
        self.on_quit = on_quit       # cleanup before the process ends (stops the Claude Code session)
        self.listener = listener     # the VoiceListener, so we can list and switch microphones
        self._mic_ids = {}           # menu id -> device index (or None for the Windows default)

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

    def _mic_submenu(self):
        """A submenu listing microphones, the current one ticked."""
        sub = win32gui.CreatePopupMenu()
        self._mic_ids = {}
        current = settings["input_device"]
        items = [(None, "Windows default")] + voice.input_devices()
        for i, (index, name) in enumerate(items):
            mid = MIC_BASE + i
            self._mic_ids[mid] = index
            flags = win32con.MF_STRING | (win32con.MF_CHECKED if index == current else win32con.MF_UNCHECKED)
            win32gui.AppendMenu(sub, flags, mid, name[:48])
        return sub

    def _on_tray(self, hwnd, msg, wparam, lparam):
        if lparam in (win32con.WM_RBUTTONUP, win32con.WM_LBUTTONUP):
            menu = win32gui.CreatePopupMenu()
            if self.listener is not None:
                win32gui.AppendMenu(menu, win32con.MF_POPUP, self._mic_submenu(), "Microphone")
                speak = win32con.MF_CHECKED if settings["speak_on_shortcut"] else win32con.MF_UNCHECKED
                win32gui.AppendMenu(menu, win32con.MF_STRING | speak, ID_SPEAK, "Speak reply on Ctrl+Alt+J")
                win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
            win32gui.AppendMenu(menu, win32con.MF_STRING, ID_PAIR, "Pair your phone (QR code)…")
            win32gui.AppendMenu(menu, win32con.MF_STRING, ID_PHONE, "Open the phone page")
            win32gui.AppendMenu(menu, win32con.MF_STRING, ID_LOG, "Open the log")
            win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
            win32gui.AppendMenu(menu, win32con.MF_STRING, ID_RESTART, "Restart Jarvis")
            win32gui.AppendMenu(menu, win32con.MF_STRING, ID_QUIT, "Quit Jarvis")
            x, y = win32gui.GetCursorPos()
            win32gui.SetForegroundWindow(hwnd)   # so clicking elsewhere dismisses the menu
            win32gui.TrackPopupMenu(menu, win32con.TPM_LEFTALIGN, x, y, 0, hwnd, None)
            win32gui.PostMessage(hwnd, win32con.WM_NULL, 0, 0)
        return True

    @staticmethod
    def _pythonw() -> Path:
        pyw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
        return pyw if pyw.exists() else Path(sys.executable).with_name("pythonw.exe")

    def _open_pairing(self) -> None:
        """The QR code and pairing link in a window of its own (its own process, so the tray stays responsive)."""
        subprocess.Popen([str(self._pythonw()), "-m", "jarvis.pairing"], cwd=str(ROOT),
                         creationflags=subprocess.CREATE_NO_WINDOW)

    @staticmethod
    def _start_again() -> None:
        """Launch a new hidden Jarvis a few seconds from now, once this one has quit (only one may run at a time)."""
        pyw = Tray._pythonw()
        later = f"import subprocess, time; time.sleep(4); subprocess.Popen([r'{pyw}', '-m', 'jarvis'], cwd=r'{ROOT}')"
        subprocess.Popen([str(pyw), "-c", later], cwd=str(ROOT), close_fds=True,
                         creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW)

    def _on_command(self, hwnd, msg, wparam, lparam):
        cmd = wparam & 0xFFFF
        if cmd in self._mic_ids and self.listener is not None:
            index = self._mic_ids[cmd]
            log.info("Microphone changed to %s from the tray.", voice.device_name(index))
            self.listener.set_device(index)
        elif cmd == ID_SPEAK:
            new = not settings["speak_on_shortcut"]
            set_setting("speak_on_shortcut", new)
            log.info("Speak-on-shortcut turned %s from the tray.", "on" if new else "off")
        elif cmd == ID_PAIR:
            log.info("Showing the pairing QR code.")
            self._open_pairing()
        elif cmd == ID_PHONE:
            url = settings["phone_page_url"]
            if url:
                webbrowser.open(url)
        elif cmd == ID_LOG:
            try:
                os.startfile(str(ROOT / "jarvis.log"))
            except Exception:
                log.debug("couldn't open the log", exc_info=True)
        elif cmd in (ID_QUIT, ID_RESTART):
            if cmd == ID_RESTART:
                log.info("Restart from the tray icon.")
                self._start_again()
            else:
                log.info("Quit from the tray icon.")
            try:
                win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (hwnd, 0))
            except Exception:
                pass
            if self.on_quit:
                try:
                    self.on_quit()
                except Exception:
                    log.debug("cleanup on quit failed", exc_info=True)
            os._exit(0)   # end the whole process, including the daemon voice/phone threads
        return True

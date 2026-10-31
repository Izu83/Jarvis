"""The Jarvis desktop app: the phone page in a window of its own on the PC, already connected.

    pythonw -m jarvis.desktop             open it (starts Jarvis too if it isn't running)
    python  -m jarvis.desktop --install   put Jarvis on the Desktop and in the Start menu

- Chat with Jarvis like on the phone (chats down the side, like Claude), Voice mode, Settings, the model picker.
- No pairing: it talks to Jarvis on this PC directly (ws://127.0.0.1:<phone_port>/desktop/ws), never through the
  internet. Its chats are its own; the phone keeps its own.
- It shows whether your phone is connected, and the Pair button opens the pairing window for a new phone.
- Closing the window doesn't stop Jarvis: he stays in the tray for your phone and "Hey Jarvis". Quit from the tray.

The page comes from docs/ through a small web server on 127.0.0.1:8788 (only this PC can reach it), in an Edge
WebView2 window (pywebview). Its storage (chats, settings) is kept in .brain/desktop-app.
"""
import ctypes
import functools
import http.server
import logging
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.config import ROOT, settings  # noqa: E402

PORT = 8788   # the page knows it's the desktop app by this address (docs/index.html: `desktop`)
TITLE = "Jarvis"
APP_ID = "Izu83.Jarvis.Desktop"   # groups the window on the taskbar under its own icon, not Python's
DOCS = ROOT / "docs"
STORE = ROOT / ".brain" / "desktop-app"
ICON = ROOT / ".brain" / "jarvis-app.ico"
HWND_FILE = ROOT / ".brain" / "desktop.hwnd"
log = logging.getLogger("jarvis.desktop")


def page_url() -> str:
    """The page, with this PC's pairing in the #fragment (never sent to the web server) pointing at Jarvis locally."""
    from jarvis.remote import b64e, ensure_keys
    topic, key = ensure_keys()
    local = f"http://127.0.0.1:{int(settings.get('phone_port', 8787))}/desktop"
    return f"http://127.0.0.1:{PORT}/index.html#t={topic}&k={b64e(key)}&s={settings['ntfy_server']}&u={local}"


def ensure_icon() -> Path:
    """Jarvis's orb as a Windows icon, in every size the taskbar and title bar ask for."""
    if not ICON.exists():
        from jarvis.orb import still
        ICON.parent.mkdir(exist_ok=True)
        still(256, bold=True, background=False).save(ICON, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                                                                    (128, 128), (256, 256)])
    return ICON


def _pythonw() -> Path:
    pyw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    return pyw if pyw.exists() else Path(sys.executable).with_name("pythonw.exe")


def jarvis_running() -> bool:
    """Is Jarvis itself running? (It holds the single-instance mutex from __main__.)"""
    handle = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, "JarvisSingleInstance_v1")   # SYNCHRONIZE
    if handle:
        ctypes.windll.kernel32.CloseHandle(handle)
    return bool(handle)


def start_jarvis() -> None:
    """The window needs Jarvis to talk to: start him (hidden, in the tray) if he isn't running."""
    if not jarvis_running():
        log.info("Jarvis isn't running; starting him.")
        subprocess.Popen([str(_pythonw()), "-m", "jarvis"], cwd=str(ROOT), close_fds=True,
                         creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW)


class _Page(http.server.SimpleHTTPRequestHandler):
    """docs/ as it is on disk, never cached, so the app always shows the latest page after an update."""

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *args):
        pass


def serve_page() -> http.server.ThreadingHTTPServer:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), functools.partial(_Page, directory=str(DOCS)))
    threading.Thread(target=server.serve_forever, daemon=True, name="desktop-page").start()
    return server


class Api:
    """What the page can ask the app for (window.pywebview.api)."""

    def let_windows_ask(self):
        """Allow was clicked here: let Windows' "Making sure it's you" box (shown for Jarvis) come to the front."""
        ctypes.windll.user32.AllowSetForegroundWindow(-1)   # ASFW_ANY

    def pair(self):
        """The pairing window (QR code), for a new phone."""
        subprocess.Popen([str(_pythonw()), "-m", "jarvis.pairing"], cwd=str(ROOT),
                         creationflags=subprocess.CREATE_NO_WINDOW)


def already_open() -> bool:
    """One window is enough: if it's open, bring it to the front instead."""
    global _mutex
    _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "JarvisDesktopWindow_v1")   # held while the app runs
    if ctypes.windll.kernel32.GetLastError() != 183:   # ERROR_ALREADY_EXISTS
        return False
    try:
        hwnd = int(HWND_FILE.read_text())
        user32 = ctypes.windll.user32
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)   # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
    except (OSError, ValueError):
        pass
    return True


def _remember_window(window) -> None:
    try:
        HWND_FILE.write_text(str(window.native.Handle.ToInt64()))
    except Exception:
        log.debug("Couldn't note the window handle", exc_info=True)


def main() -> None:
    logging.basicConfig(filename=str(ROOT / "jarvis.log"), level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    if already_open():
        return
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    start_jarvis()
    try:
        serve_page()
    except OSError:
        ctypes.windll.user32.MessageBoxW(None, f"Something else is using port {PORT}, which the Jarvis app needs.",
                                         TITLE, 0x10)
        return
    import webview
    STORE.mkdir(parents=True, exist_ok=True)
    window = webview.create_window(TITLE, page_url(), js_api=Api(), width=1220, height=840, min_size=(720, 560),
                                   background_color="#0b0d12", text_select=True)
    window.events.shown += lambda: _remember_window(window)
    log.info("Desktop app opened.")
    webview.start(gui="edgechromium", private_mode=False, storage_path=str(STORE), icon=str(ensure_icon()))
    HWND_FILE.unlink(missing_ok=True)


def install() -> list[Path]:
    """Jarvis on the Desktop and in the Start menu (so Windows search finds it, and it can be pinned)."""
    import pythoncom
    from win32com.client import Dispatch
    from win32com.propsys import propsys, pscon
    from win32com.shell import shell, shellcon

    places = [Path(shell.SHGetFolderPath(0, shellcon.CSIDL_DESKTOPDIRECTORY, None, 0)),
              Path(shell.SHGetFolderPath(0, shellcon.CSIDL_PROGRAMS, None, 0))]
    made = []
    for folder in places:
        link = folder / "Jarvis.lnk"
        sc = Dispatch("WScript.Shell").CreateShortcut(str(link))
        sc.TargetPath = str(_pythonw())
        sc.Arguments = "-m jarvis.desktop"
        sc.WorkingDirectory = str(ROOT)
        sc.IconLocation = str(ensure_icon())
        sc.Description = "Chat with Jarvis"
        sc.Save()
        # the same app ID as the window, so a pinned shortcut and the open window share one taskbar button
        store = propsys.SHGetPropertyStoreFromParsingName(str(link), None, shellcon.GPS_READWRITE,
                                                          propsys.IID_IPropertyStore)
        store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(APP_ID, pythoncom.VT_LPWSTR))
        store.Commit()
        made.append(link)
    return made


if __name__ == "__main__":
    if "--install" in sys.argv:
        for path in install():
            print("Made", path)
    else:
        main()

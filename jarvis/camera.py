"""Grab one photo from the PC's webcam as JPEG bytes. Never sent to Claude, so it costs no tokens.

The main Jarvis venv has no OpenCV, but the Gesture-App's Python does, so we borrow it (the same trick as
gesture.py) to snap a single frame in a short-lived subprocess, then read the file back.
"""
import ctypes
import logging
import subprocess
import tempfile
import threading
import time
import uuid
from ctypes import wintypes
from pathlib import Path

from . import gesture

log = logging.getLogger("jarvis.camera")


class NoCamera(RuntimeError):
    """Windows doesn't see a webcam at all (unplugged), as opposed to one that's busy."""


# Windows device classes webcams show up under: "Camera" (newer drivers) and "Imaging devices" (older ones)
_CAMERA_CLASSES = ("ca3e7ab9-b4c3-4ae6-8251-579ef933890f", "6bdd1fc6-810f-11d0-bec7-08002be2092f")


class _DevInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("ClassGuid", ctypes.c_byte * 16), ("DevInst", wintypes.DWORD),
                ("Reserved", ctypes.c_void_p)]


def present() -> bool:
    """Is a webcam plugged in right now? Asks Windows' device list directly: instant, no camera opened."""
    setup = ctypes.windll.setupapi
    setup.SetupDiGetClassDevsW.restype = ctypes.c_void_p
    for cls in _CAMERA_CLASSES:
        guid = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID(cls).bytes_le)
        devs = setup.SetupDiGetClassDevsW(ctypes.byref(guid), None, None, 0x2)   # DIGCF_PRESENT
        if not devs or devs == ctypes.c_void_p(-1).value:
            continue
        try:
            info = _DevInfo(cbSize=ctypes.sizeof(_DevInfo))
            if setup.SetupDiEnumDeviceInfo(ctypes.c_void_p(devs), 0, ctypes.byref(info)):
                return True
        finally:
            setup.SetupDiDestroyDeviceInfoList(ctypes.c_void_p(devs))
    return False


_watching = threading.Lock()


def tell_when_back(callback, every: float = 10, give_up_after: float = 24 * 3600) -> None:
    """After a photo failed because no webcam was plugged in: call `callback` once it's connected again."""
    if not _watching.acquire(blocking=False):
        return   # already watching

    def watch():
        try:
            end = time.time() + give_up_after
            while time.time() < end:
                time.sleep(every)
                if present():
                    log.info("The webcam is connected again.")
                    callback()
                    return
        finally:
            _watching.release()

    threading.Thread(target=watch, daemon=True, name="camera-back").start()

# Run by the cv2-capable Python. Opens the camera, reads a few frames so it can auto-expose, saves one JPEG.
# argv for `python -c "<script>" <out> <index>` is ["-c", out, index]. Exit 2 = no camera to open, 1 = it wouldn't read.
_SNAP = (
    "import sys, cv2\n"
    "cam = cv2.VideoCapture(int(sys.argv[2]), cv2.CAP_DSHOW)\n"
    "if not cam.isOpened():\n"
    "    cam = cv2.VideoCapture(int(sys.argv[2]), cv2.CAP_MSMF)\n"
    "if not cam.isOpened():\n"
    "    sys.exit(2)\n"
    "cam.set(cv2.CAP_PROP_FRAME_WIDTH, 1280); cam.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)\n"
    "ok, frame = False, None\n"
    "for _ in range(4):\n"          # a few frames so the camera auto-exposes; fewer = faster
    "    ok, frame = cam.read()\n"
    "cam.release()\n"
    "sys.exit(0 if ok and frame is not None and "
    "cv2.imwrite(sys.argv[1], frame, [cv2.IMWRITE_JPEG_QUALITY, 82]) else 1)\n"
)

_exe: str | None = None   # cache the cv2-capable Python so we don't re-probe (an extra cv2 import) every photo


def _python() -> str | None:
    global _exe
    if _exe is None:
        _exe = gesture._python() or ""   # "" = probed and none found; don't keep re-probing
    return _exe or None


def available() -> bool:
    return _python() is not None


def capture(index: int = 0) -> bytes:
    """One JPEG from the webcam. Raises RuntimeError if no camera/OpenCV is available or the grab fails."""
    exe = _python()
    if not exe:
        raise RuntimeError("no camera library found (install opencv-python in your Python 3.13)")
    out = Path(tempfile.gettempdir()) / "jarvis_cam.jpg"
    out.unlink(missing_ok=True)
    try:
        r = subprocess.run([exe, "-c", _SNAP, str(out), str(index)], capture_output=True, timeout=30,
                           creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"couldn't start the camera: {e}") from e
    if r.returncode == 2:
        raise NoCamera("Windows can't see a webcam right now. Check it's plugged in, then try again")
    if r.returncode != 0 or not out.exists():
        raise RuntimeError("the webcam wouldn't give a picture (is another app using it?)")
    data = out.read_bytes()
    out.unlink(missing_ok=True)
    log.info("Captured a %d KB webcam photo.", len(data) // 1024)
    return data

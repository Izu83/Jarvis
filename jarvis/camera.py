"""Grab one photo from the PC's webcam as JPEG bytes. Never sent to Claude, so it costs no tokens.

The main Jarvis venv has no OpenCV, but the Gesture-App's Python does, so we borrow it (the same trick as
gesture.py) to snap a single frame in a short-lived subprocess, then read the file back.
"""
import logging
import subprocess
import tempfile
from pathlib import Path

from . import gesture

log = logging.getLogger("jarvis.camera")

# Run by the cv2-capable Python. Opens the camera, reads a few frames so it can auto-expose, saves one JPEG.
# argv for `python -c "<script>" <out> <index>` is ["-c", out, index].
_SNAP = (
    "import sys, cv2\n"
    "cam = cv2.VideoCapture(int(sys.argv[2]), cv2.CAP_DSHOW)\n"
    "cam.set(cv2.CAP_PROP_FRAME_WIDTH, 1280); cam.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)\n"
    "ok, frame = False, None\n"
    "for _ in range(8):\n"
    "    ok, frame = cam.read()\n"
    "cam.release()\n"
    "sys.exit(0 if ok and frame is not None and "
    "cv2.imwrite(sys.argv[1], frame, [cv2.IMWRITE_JPEG_QUALITY, 82]) else 1)\n"
)


def available() -> bool:
    return gesture._python() is not None


def capture(index: int = 0) -> bytes:
    """One JPEG from the webcam. Raises RuntimeError if no camera/OpenCV is available or the grab fails."""
    exe = gesture._python()
    if not exe:
        raise RuntimeError("no camera library found (install opencv-python in your Python 3.13)")
    out = Path(tempfile.gettempdir()) / "jarvis_cam.jpg"
    out.unlink(missing_ok=True)
    try:
        r = subprocess.run([exe, "-c", _SNAP, str(out), str(index)], capture_output=True, timeout=30,
                           creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"couldn't start the camera: {e}") from e
    if r.returncode != 0 or not out.exists():
        raise RuntimeError("couldn't read from the webcam (is it connected and not in use by another app?)")
    data = out.read_bytes()
    out.unlink(missing_ok=True)
    log.info("Captured a %d KB webcam photo.", len(data) // 1024)
    return data

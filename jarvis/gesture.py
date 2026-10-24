"""Launch the user's Gesture-App (webcam hand-gesture control) from Jarvis.

Gesture-App lives in its own project folder and needs opencv/mediapipe, which aren't in Jarvis's venv, so it runs
with whichever Python has those installed (Python 3.13 on this PC).
"""
import logging
import subprocess
from pathlib import Path

import psutil

from .config import settings

log = logging.getLogger("jarvis.gesture")

_proc: subprocess.Popen | None = None


def app_dir() -> Path:
    return Path(settings.get("gesture_app", r"D:\Projects\Gesture-App"))


def _python() -> str | None:
    """A Python that can import cv2 (fast to test). Prefer the configured one, then common install paths."""
    import os
    candidates = []
    if settings.get("gesture_python"):
        candidates.append(settings["gesture_python"])
    local = os.environ.get("LOCALAPPDATA", "")
    candidates += [
        rf"{local}\Programs\Python\Python313\python.exe",
        rf"{local}\Programs\Python\Python312\python.exe",
    ]
    for exe in candidates:
        if exe and Path(exe).exists():
            try:
                r = subprocess.run([exe, "-c", "import cv2"], capture_output=True, timeout=25,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
                if r.returncode == 0:
                    return exe
            except (OSError, subprocess.TimeoutExpired):
                continue
    return None


def _find_running() -> psutil.Process | None:
    """Gesture-App's own camera.py, not any other program that happens to have a camera.py."""
    folder = str(app_dir()).lower().rstrip("\\/")
    for p in psutil.process_iter(["cmdline", "cwd"]):
        try:
            args = [str(a) for a in (p.info["cmdline"] or [])]
            script = next((a for a in args if a.lower().endswith("camera.py")), None)
            if not script:
                continue
            if script.lower().startswith(folder) or (p.info["cwd"] or "").lower().rstrip("\\/") == folder:
                return p
        except psutil.Error:
            continue
    return None


def running() -> bool:
    global _proc
    if _proc and _proc.poll() is None:
        return True
    return _find_running() is not None


def start() -> str:
    global _proc
    if running():
        return "Gesture control is already running. Show your hand to the webcam."
    folder = app_dir()
    entry = folder / "src" / "camera.py"
    if not entry.exists():
        return f"I couldn't find Gesture-App at {folder}. Set 'gesture_app' in config.json to its folder."
    python = _python()
    if not python:
        return ("I couldn't find a Python with the camera libraries installed. In Gesture-App run "
                "'pip install opencv-python mediapipe pillow numpy faster-whisper', then try again.")
    try:
        _proc = subprocess.Popen([python, str(entry)], cwd=str(folder),
                                 creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    except OSError as e:
        return f"I couldn't start Gesture-App: {e}"
    log.info("Started Gesture-App (pid %s) with %s", _proc.pid, python)
    return "Gesture control is on. A camera window opened on your PC. Make the Help sign (both hands) to see the gestures."


def stop() -> str:
    global _proc
    proc = _find_running()
    if not proc and not (_proc and _proc.poll() is None):
        return "Gesture control isn't running."
    for p in ([proc] if proc else []) + ([psutil.Process(_proc.pid)] if _proc and _proc.poll() is None else []):
        try:
            p.terminate()
        except psutil.Error:
            pass
    _proc = None
    return "Turned gesture control off."


def toggle() -> str:
    return stop() if running() else start()

"""What's playing in any app (Spotify, YouTube in the browser, ...) and its album art, via Windows' media
session API: the same source as the media popup next to the volume flyout."""
import asyncio
import hashlib
import io
import logging
import os
import threading
import time

from PIL import Image

# winrt is imported inside the functions: it ships an older msvcp140.dll, and loading it before onnxruntime
# (the wake word) crashes the process. See the top of __main__.py.

log = logging.getLogger("jarvis.media")

PLAYING = 4  # GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING
APP_NAMES = {"spotify": "Spotify", "opera": "Opera", "chrome": "Chrome", "msedge": "Edge", "firefox": "Firefox",
             "vlc": "VLC", "zunemusic": "Media Player", "discord": "Discord", "youtube": "YouTube"}
ART_SIZE = 320
_art: dict[str, bytes] = {}  # art id -> JPEG, most recent last


def _app_name(aumid: str) -> str:
    low = aumid.lower()
    return next((name for key, name in APP_NAMES.items() if key in low), aumid.split("!")[0].split(".")[-1])


async def _read_art(props) -> bytes | None:
    from winrt.windows.storage.streams import Buffer, InputStreamOptions

    if not props.thumbnail:
        return None
    stream = await props.thumbnail.open_read_async()
    buf = Buffer(stream.size)
    await stream.read_async(buf, stream.size, InputStreamOptions.READ_AHEAD)
    img = Image.open(io.BytesIO(bytes(buf))).convert("RGB")
    # Video thumbnails are 16:9; crop to a square like an album cover
    side = min(img.size)
    img = img.crop(((img.width - side) // 2, (img.height - side) // 2, (img.width + side) // 2, (img.height + side) // 2))
    img.thumbnail((ART_SIZE, ART_SIZE))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=82)
    return out.getvalue()


async def _now() -> dict | None:
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager

    mgr = await Manager.request_async()
    sessions = list(mgr.get_sessions())
    if not sessions:
        return None
    # Prefer whatever is actually playing; otherwise the session Windows considers current
    playing = [s for s in sessions if s.get_playback_info().playback_status == PLAYING]
    session = playing[0] if playing else (mgr.get_current_session() or sessions[0])
    props = await session.try_get_media_properties_async()
    is_playing = session.get_playback_info().playback_status == PLAYING
    tl = session.get_timeline_properties()
    position = tl.position.total_seconds()
    if is_playing and tl.last_updated_time:  # apps only report position now and then; extrapolate
        position += max(0.0, time.time() - tl.last_updated_time.timestamp())
    duration = tl.end_time.total_seconds()
    art_id = hashlib.sha1(f"{props.title}|{props.artist}|{props.album_title}".encode()).hexdigest()[:12]
    if art_id not in _art:
        try:
            data = await _read_art(props)
        except Exception:
            log.debug("couldn't read album art", exc_info=True)
            data = None
        if data:
            _art[art_id] = data
            while len(_art) > 30:
                _art.pop(next(iter(_art)))
    return {
        "playing": is_playing,
        "title": props.title or "",
        "artist": props.artist or "",
        "album": props.album_title or "",
        "app": _app_name(session.source_app_user_model_id),
        "position": round(min(position, duration) if duration else position, 1),
        "duration": round(duration, 1),
        "art": art_id if art_id in _art else None,
    }


# Windows' media service (NPSMSvc) sometimes hangs. A request to it then never ends: it stays stuck in a Windows worker
# thread for good, and once Windows' media library is loaded it keeps starting more of them on its own (40 a second,
# 14,000 in two days) until Jarvis crawled and the phone couldn't connect. So the reading happens in a helper process
# (media_helper.py) that is killed, with all its stuck threads, when it doesn't answer or its threads start piling up.
# After a read that doesn't answer, wait before asking again (longer each time), and only one read at a time.
TIMEOUT, START_TIMEOUT = 4, 20
MAX_HELPER_THREADS = 150
_backoff = {"until": 0.0, "wait": 0.0, "busy": False}
_backoff_lock = threading.Lock()
FIRST_WAIT, MAX_WAIT = 30.0, 1800.0
_helper: dict = {"proc": None, "lines": None}
HELPER = ["-m", "jarvis.media_helper"]


def _stop_helper() -> None:
    proc, _helper["proc"] = _helper["proc"], None
    if not proc:
        return
    import psutil
    try:   # the venv's python.exe is a small launcher that starts the real one: end both
        kids = psutil.Process(proc.pid).children(recursive=True)
    except Exception:
        kids = []
    for p in kids:
        try:
            p.kill()
        except Exception:
            pass
    try:
        proc.kill()
    except Exception:
        pass


def _helper_threads(proc) -> int:
    import psutil
    try:
        me = psutil.Process(proc.pid)
        return sum(p.num_threads() for p in [me, *me.children(recursive=True)])
    except Exception:
        return 0


def _ask() -> dict | None:
    """What's playing, from the helper process. Raises TimeoutError when Windows doesn't answer."""
    import json
    import queue
    import subprocess
    import sys

    from .config import ROOT

    proc, fresh = _helper["proc"], False
    if proc and (proc.poll() is not None or _helper_threads(proc) > MAX_HELPER_THREADS):
        if proc.poll() is None:
            log.warning("Windows' media service is stuck again (%d threads); restarting the media helper.",
                        _helper_threads(proc))
        _stop_helper()
        proc = None
    if proc is None:
        exe = sys.executable.replace("pythonw.exe", "python.exe")
        proc = subprocess.Popen([exe, *HELPER, str(os.getpid())], cwd=ROOT,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                encoding="utf-8", bufsize=1, creationflags=subprocess.CREATE_NO_WINDOW)
        lines: queue.Queue = queue.Queue()

        def pump(out=proc.stdout, q=lines):
            for line in out:
                q.put(line)
            q.put(None)

        threading.Thread(target=pump, daemon=True, name="media-helper").start()
        _helper.update(proc=proc, lines=lines)
        fresh = True
    lines = _helper["lines"]
    try:
        proc.stdin.write("now\n")
        proc.stdin.flush()
        line = lines.get(timeout=START_TIMEOUT if fresh else TIMEOUT)
    except queue.Empty:
        _stop_helper()
        raise TimeoutError from None
    except OSError:
        _stop_helper()
        return None
    if line is None:
        _stop_helper()
        return None
    reply = json.loads(line)
    if reply.get("error"):   # the service refused (it does that while hung): back off like a timeout
        log.debug("media helper: %s", reply["error"])
        raise TimeoutError(reply["error"])
    info = reply.get("info")
    if info and info.get("art") and reply.get("art"):
        import base64
        _art[info["art"]] = base64.b64decode(reply["art"])
        while len(_art) > 30:
            _art.pop(next(iter(_art)))
    if info and info.get("art") not in _art:
        info["art"] = None
    return info


def now_playing() -> dict | None:
    with _backoff_lock:
        if _backoff["busy"] or time.time() < _backoff["until"]:
            return None
        _backoff["busy"] = True
    try:
        info = _ask()
        with _backoff_lock:
            _backoff["wait"] = 0.0
        return info
    except (asyncio.TimeoutError, TimeoutError):
        with _backoff_lock:
            _backoff["wait"] = min(MAX_WAIT, _backoff["wait"] * 2 or FIRST_WAIT)
            _backoff["until"] = time.time() + _backoff["wait"]
            wait = _backoff["wait"]
        log.warning("Windows' media service didn't answer; not asking it what's playing for %d s.", wait)
        return None
    except Exception:
        log.debug("media session unavailable", exc_info=True)
        return None
    finally:
        with _backoff_lock:
            _backoff["busy"] = False


def art(art_id: str) -> bytes | None:
    return _art.get(art_id)

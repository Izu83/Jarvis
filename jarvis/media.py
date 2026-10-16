"""What's playing in any app (Spotify, YouTube in the browser, ...) and its album art, via Windows' media
session API: the same source as the media popup next to the volume flyout."""
import asyncio
import hashlib
import io
import logging
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


def now_playing() -> dict | None:
    try:
        return asyncio.run(asyncio.wait_for(_now(), 4))
    except Exception:
        log.debug("media session unavailable", exc_info=True)
        return None


def art(art_id: str) -> bytes | None:
    return _art.get(art_id)

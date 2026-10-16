"""Play specific things on Spotify by opening its own URIs (no API key needed).

Spotify registers the `spotify:` protocol, so os.startfile("spotify:search:…") opens search, and the desktop app
plays the top result when we send it the Play media key a moment later. Playlists/albums by name need a lookup we
don't have, so for "my liked songs" etc. we open the matching built-in view.
"""
import ctypes
import os
import time
import urllib.parse

from . import apps

PLAY = 0xB3  # VK_MEDIA_PLAY_PAUSE

# Spoken playlist names -> the Spotify URI that opens them
VIEWS = {
    "liked songs": "spotify:collection:tracks",
    "liked": "spotify:collection:tracks",
    "your library": "spotify:collection",
    "discover weekly": "spotify:playlist:discover-weekly",
    "release radar": "spotify:playlist:release-radar",
    "daily mix": "spotify:collection",
}


def _press_play() -> None:
    ctypes.windll.user32.keybd_event(PLAY, 0, 0, 0)
    ctypes.windll.user32.keybd_event(PLAY, 0, 2, 0)


def play(query: str) -> str:
    """Search Spotify for `query` and start the top result. Empty query just resumes playback."""
    q = query.strip()
    if not q:
        _press_play()
        return "Resumed playback."
    view = VIEWS.get(q.lower())
    _ensure_open()
    if view:
        os.startfile(view)
        time.sleep(2.5)
        _press_play()
        return f"Opening {q} on Spotify."
    uri = "spotify:search:" + urllib.parse.quote(q)
    os.startfile(uri)
    time.sleep(2.8)  # let the results load, then play the top hit
    _press_play()
    return f"Playing “{q}” on Spotify."


def _ensure_open() -> None:
    from . import windows as win
    if not any(w.app == "spotify" for w in win.list_windows()):
        apps.open_app("Spotify")
        time.sleep(3.0)

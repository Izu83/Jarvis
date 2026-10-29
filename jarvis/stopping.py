"""Stopping the command Jarvis is working on: the phone's stop button and the tray's "Stop what Jarvis is doing".

stop() sets a flag the brains check between steps and runs the stop hooks (the Claude Code brain kills its session
outright, so a long answer or tool run doesn't carry on; the phone drops its waiting permission prompts). The
command then unwinds with Stopped, which run_job turns into "Okay, I stopped."
"""
import logging
import re
import subprocess
import threading

log = logging.getLogger("jarvis.stopping")

# Said (or typed) while Jarvis is busy, these stop the command instead of waiting their turn in the queue.
# When he's idle they're ordinary commands ("stop" on its own then pauses the music, as before).
_STOP_PHRASE = re.compile(
    r"^(?:(?:hey\s+|хей\s+)?(?:jarvis|джарвис)[,.!]?\s+)?(?:please\s+|моля\s+)?"
    r"(?:stop|cancel|abort|halt|enough|never\s?mind|forget it|"
    r"stop (?:it|that|this|now|working|everything|what you'?re doing)|cancel (?:it|that|this)|"
    r"спри|стоп|стига|откажи|няма значение|спри (?:се|това|веднага))"
    r"(?:\s+(?:please|jarvis|now|моля|джарвис))?[\s.!]*$", re.I)


def is_stop_phrase(text: str) -> bool:
    return bool(_STOP_PHRASE.match(text.strip()))

_event = threading.Event()
_hooks: list = []


class Stopped(BaseException):
    """The user pressed stop. A BaseException (like KeyboardInterrupt), so the brains' `except Exception` blocks
    don't catch it and hand the command to the next brain instead of stopping."""


def begin() -> None:
    """A new command starts: forget any earlier stop."""
    _event.clear()


def requested() -> bool:
    return _event.is_set()


def check() -> None:
    if _event.is_set():
        raise Stopped()


def on_stop(fn) -> None:
    _hooks.append(fn)


def stop() -> None:
    _event.set()
    for fn in list(_hooks):
        try:
            fn()
        except Exception:
            log.exception("A stop hook failed")


def wait(fn, *args, **kwargs):
    """Run a blocking call (a request to the local model, say), but give up as soon as stop is pressed. The call
    itself carries on in the background; its result is thrown away."""
    box: dict = {}

    def run():
        try:
            box["value"] = fn(*args, **kwargs)
        except BaseException as e:
            box["error"] = e

    t = threading.Thread(target=run, daemon=True, name="stoppable")
    t.start()
    while t.is_alive():
        t.join(0.2)
        if _event.is_set():
            raise Stopped()
    if "error" in box:
        raise box["error"]
    return box["value"]


def kill_tree(pid: int) -> None:
    """End a process and everything it started (Claude Code and its tool server)."""
    subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True,
                   creationflags=subprocess.CREATE_NO_WINDOW)

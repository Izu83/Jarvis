"""Reads what's playing from Windows' media service, in its own process (started by media.py).

When that service hangs, every request to it gets stuck in a Windows worker thread for good, and the stuck threads
keep multiplying (thousands per hour). In here they don't matter: Jarvis kills this process and all of them die with it.

Protocol: one line "now" on stdin -> one JSON line on stdout: {"info": {...} | null, "art": base64 JPEG | null,
"error": text | null}.
"""
import asyncio
import base64
import json
import os
import sys
import threading
import time

import psutil


def _watch_parent(pid: int) -> None:
    # A request stuck in Windows never notices stdin closing, so also leave when Jarvis is gone.
    while True:
        time.sleep(5)
        if not psutil.pid_exists(pid):
            os._exit(0)


def main() -> None:
    from jarvis import media   # winrt is only imported in here, never in Jarvis itself

    if len(sys.argv) > 1:
        threading.Thread(target=_watch_parent, args=(int(sys.argv[1]),), daemon=True).start()
    sent: set[str] = set()
    for line in sys.stdin:
        if line.strip() != "now":
            continue
        try:
            info, error = asyncio.run(media._now()), None
        except Exception as e:   # e.g. "Call was canceled by the message filter" while the service hangs
            info, error = None, f"{type(e).__name__}: {e}"
        art = None
        if info and info.get("art") and info["art"] not in sent:
            data = media.art(info["art"])
            if data:
                art = base64.b64encode(data).decode()
                sent.add(info["art"])
        sys.stdout.write(json.dumps({"info": info, "art": art, "error": error}) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()

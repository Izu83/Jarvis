"""Slow readings kept fresh in the background (sysinfo.Fresh), so the phone's status never waits on them."""
import threading
import time

from jarvis import sysinfo


def test_a_slow_reading_never_holds_up_the_status():
    calls = []

    def slow():
        calls.append(time.time())
        time.sleep(0.3)
        return len(calls)

    ready = threading.Event()
    fresh = sysinfo.Fresh("test", slow, every=0.05)
    t = time.time()
    assert fresh.get(on_ready=ready.set) is None          # nothing yet, and no waiting for it
    assert time.time() - t < 0.1
    assert ready.wait(2)                                    # the first reading is in: told once
    assert fresh.get() >= 1
    fresh.IDLE = 0                                          # nobody asking any more: the thread stops
    deadline = time.time() + 2
    while fresh.running and time.time() < deadline:
        time.sleep(0.05)
    assert not fresh.running


def test_asking_with_wait_reads_it_when_there_is_nothing_yet():
    fresh = sysinfo.Fresh("test-wait", lambda: "now", every=10)
    fresh.IDLE = 0
    assert fresh.get(wait=True) == "now"

import threading
import time

import pytest

from jarvis import stopping


def test_stop_ends_a_slow_call_quickly():
    stopping.begin()
    threading.Timer(0.3, stopping.stop).start()
    started = time.time()
    with pytest.raises(stopping.Stopped):
        stopping.wait(time.sleep, 30)
    assert time.time() - started < 2
    stopping.begin()
    assert not stopping.requested()


def test_wait_passes_results_and_errors_through():
    stopping.begin()
    assert stopping.wait(lambda a, b=0: a + b, 2, b=3) == 5
    with pytest.raises(ValueError):
        stopping.wait(lambda: int("x"))


def test_brains_cannot_swallow_a_stop():
    """The brains catch Exception to hand a command to the next brain; a stop must get past that."""
    stopping.begin()
    stopping.stop()
    reached = False
    with pytest.raises(stopping.Stopped):
        try:
            stopping.check()
        except Exception:
            reached = True
    assert not reached
    stopping.begin()


def test_hooks_run_and_a_failing_hook_does_not_block_the_rest():
    ran = []
    stopping.on_stop(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    stopping.on_stop(lambda: ran.append(1))
    stopping.stop()
    assert ran == [1]
    stopping._hooks.clear()
    stopping.begin()

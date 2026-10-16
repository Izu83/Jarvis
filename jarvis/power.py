"""Power: sleep, restart, shut down, sign out. Each is gated by approval in tools.py."""
import ctypes
import subprocess


def sleep() -> str:
    # Suspend (false = sleep, not hibernate). Turn hibernation off first so this really sleeps.
    subprocess.run(["powercfg", "-h", "off"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    ok = ctypes.windll.powrprof.SetSuspendState(False, True, False)
    subprocess.Popen(["powercfg", "-h", "on"], creationflags=subprocess.CREATE_NO_WINDOW)
    return "Putting the PC to sleep." if ok else "Couldn't put the PC to sleep."


def _shutdown(args: list[str], what: str) -> str:
    subprocess.Popen(["shutdown", *args], creationflags=subprocess.CREATE_NO_WINDOW)
    return what


def restart() -> str:
    return _shutdown(["/r", "/t", "8"], "Restarting the PC in a few seconds.")


def shutdown() -> str:
    return _shutdown(["/s", "/t", "8"], "Shutting the PC down in a few seconds.")


def sign_out() -> str:
    return _shutdown(["/l"], "Signing you out.")


def cancel() -> str:
    subprocess.run(["shutdown", "/a"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return "Cancelled the pending shutdown or restart."

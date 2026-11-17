"""Detailed system stats for the phone's System page: CPU, GPU, memory, disks, network and top apps."""
import ctypes
import platform
import subprocess
import threading
import time
import winreg

import psutil

IDLE_PROCESSES = {"system idle process", "idle", "system", "registry", "memory compression"}
FRIENDLY = {
    "msedgewebview2": "Web apps (WebView2)", "dwm": "Windows graphics", "explorer": "File Explorer",
    "code": "VS Code", "msedge": "Edge", "chrome": "Chrome", "opera": "Opera", "claude": "Claude",
    "spotify": "Spotify", "discord": "Discord", "steam": "Steam", "steamwebhelper": "Steam",
    "python": "Python", "node": "Node.js", "msmpeng": "Windows Defender", "searchhost": "Windows Search",
}


def _cpu_name() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
            return " ".join(winreg.QueryValueEx(k, "ProcessorNameString")[0].split())
    except OSError:
        return platform.processor()


CPU_NAME = _cpu_name()
_net_last = (time.time(), psutil.net_io_counters())
_cpu = {"pct": 0.0, "per_core": []}


def _sample_cpu() -> None:
    """CPU % is only meaningful over an interval, so measure it continuously in the background (1 s windows)."""
    while True:
        per_core = psutil.cpu_percent(interval=1, percpu=True)
        _cpu["per_core"] = per_core
        _cpu["pct"] = sum(per_core) / len(per_core) if per_core else 0.0


threading.Thread(target=_sample_cpu, daemon=True, name="cpu-sampler").start()


def cpu_now() -> int:
    return round(_cpu["pct"])


class Fresh:
    """A slow reading kept up to date in a background thread while something keeps asking for it, so whoever asks
    gets the latest value at once instead of waiting seconds (the phone's status waited ~7 s for the busiest app).

    The thread stops IDLE seconds after the last ask; the next ask starts it again, and when its first value lands
    it calls that ask's on_ready (so the phone can get a status with it right away)."""
    IDLE = 90

    def __init__(self, name: str, read, every: float, warm: bool = False):
        self.name, self.read, self.every, self.warm = name, read, every, warm
        self.value, self.at = None, 0.0
        self.asked, self.running, self.on_ready = 0.0, False, None
        self.lock = threading.Lock()

    def get(self, on_ready=None, wait: bool = False):
        """The latest value (None before the first). wait: read it now if there isn't one yet."""
        with self.lock:
            self.asked = time.time()
            if not self.running:
                self.running, self.on_ready = True, on_ready
                threading.Thread(target=self._run, daemon=True, name=self.name).start()
        if self.value is None and wait:
            return self.read()
        return self.value

    def _run(self) -> None:
        first = True
        if self.warm:   # counters that measure "since the last call" need a call to start from
            try:
                self.read()
            except Exception:
                pass
            time.sleep(1)
        while True:
            with self.lock:
                if time.time() - self.asked >= self.IDLE:
                    self.running = False
                    return
            try:
                self.value, self.at = self.read(), time.time()
            except Exception:
                pass
            if first:
                first = False
                with self.lock:
                    ready, self.on_ready = self.on_ready, None
                if ready:
                    try:
                        ready()
                    except Exception:
                        pass
            time.sleep(self.every)


_cpu_times: dict[int, tuple[float, float]] = {}   # pid -> (when, CPU seconds used by then)


def _all_apps(limit: int = 10) -> list[dict]:
    """Apps by CPU since the last call, with their processes added up (Chrome has dozens). Slow: seconds with a few
    hundred processes, so the phone reads it through TOP_APPS. CPU comes from the CPU time used since the last call,
    read in the same pass as the memory (psutil's cpu_percent would make a second, equally slow pass)."""
    usage: dict[str, dict] = {}
    now, seen = time.time(), {}
    for p in psutil.process_iter(["name", "memory_info", "cpu_times"]):
        try:
            raw = (p.info["name"] or "").removesuffix(".exe")
            if raw.lower() in IDLE_PROCESSES:
                continue
            name = FRIENDLY.get(raw.lower(), raw[:1].upper() + raw[1:])
            a = usage.setdefault(name, {"name": name, "cpu": 0.0, "mem": 0})
            times = p.info["cpu_times"]
            if times:
                used = times.user + times.system
                seen[p.pid] = (now, used)
                last = _cpu_times.get(p.pid)
                if last and now > last[0] and used >= last[1]:
                    a["cpu"] += (used - last[1]) / (now - last[0]) * 100
            a["mem"] += p.info["memory_info"].rss if p.info["memory_info"] else 0
        except psutil.Error:
            continue
    _cpu_times.clear()
    _cpu_times.update(seen)
    cores = psutil.cpu_count() or 1
    top = sorted(usage.values(), key=lambda a: (a["cpu"], a["mem"]), reverse=True)[:limit]
    return [{"name": a["name"], "cpu": round(a["cpu"] / cores, 1), "mem": a["mem"]} for a in top]


TOP_APPS = Fresh("top-apps", _all_apps, every=10, warm=True)


def apps(limit: int = 5, on_ready=None, wait: bool = True) -> list[dict]:
    """The busiest apps, from the background reading (wait=False: [] until the first one is in)."""
    return (TOP_APPS.get(on_ready, wait=wait) or [])[:limit]


def gpus() -> list[dict]:
    """NVIDIA cards via nvidia-smi (ships with the driver). Empty list if there isn't one."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,fan.speed",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=4, creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    result = []
    for line in out.strip().splitlines():
        f = [x.strip() for x in line.split(",")]
        num = lambda s: float(s) if s.replace(".", "", 1).isdigit() else None  # noqa: E731
        if len(f) >= 6:
            result.append({"name": f[0].replace("NVIDIA ", ""), "load": num(f[1]), "vram_used": num(f[2]),
                           "vram_total": num(f[3]), "temp": num(f[4]), "fan": num(f[5])})
    return result


def _volume_label(root: str) -> str:
    buf = ctypes.create_unicode_buffer(261)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(root, buf, 261, None, None, None, None, 0)
    return buf.value if ok else ""


def disks() -> list[dict]:
    out = []
    for part in psutil.disk_partitions(all=False):
        if "cdrom" in part.opts or not part.fstype:
            continue
        try:
            u = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        out.append({"name": part.mountpoint.rstrip("\\"), "label": _volume_label(part.mountpoint),
                    "used": u.used, "total": u.total, "free": u.free, "pct": round(u.percent)})
    return out


def network() -> dict:
    """Download/upload speed in bytes per second since the last call."""
    global _net_last
    now, counters = time.time(), psutil.net_io_counters()
    then, prev = _net_last
    _net_last = (now, counters)
    dt = max(now - then, 0.5)
    return {"down": round((counters.bytes_recv - prev.bytes_recv) / dt),
            "up": round((counters.bytes_sent - prev.bytes_sent) / dt)}


def snapshot() -> dict:
    vm = psutil.virtual_memory()
    freq = psutil.cpu_freq()
    return {
        "host": platform.node(),
        "os": f"Windows {platform.release()}",
        "uptime": int(time.time() - psutil.boot_time()),
        "cpu": {"name": CPU_NAME, "pct": cpu_now(), "cores": psutil.cpu_count(logical=False),
                "threads": psutil.cpu_count(), "ghz": round(freq.current / 1000, 2) if freq else None,
                "per_core": [round(x) for x in _cpu["per_core"]]},
        "ram": {"used": vm.used, "total": vm.total, "pct": round(vm.percent)},
        "gpus": gpus(),
        "disks": disks(),
        "net": network(),
        "apps": apps(),
    }

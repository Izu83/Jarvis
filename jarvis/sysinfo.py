"""Detailed system stats for the phone's System page: CPU, GPU, memory, disks, network and top apps."""
import ctypes
import platform
import subprocess
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
psutil.cpu_percent(None)
psutil.cpu_percent(None, percpu=True)


def apps(limit: int = 5) -> list[dict]:
    """Apps by CPU since the last call, with their processes added up (Chrome has dozens)."""
    usage: dict[str, dict] = {}
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            raw = (p.info["name"] or "").removesuffix(".exe")
            if raw.lower() in IDLE_PROCESSES:
                continue
            name = FRIENDLY.get(raw.lower(), raw[:1].upper() + raw[1:])
            a = usage.setdefault(name, {"name": name, "cpu": 0.0, "mem": 0})
            a["cpu"] += p.cpu_percent(None)
            a["mem"] += p.info["memory_info"].rss if p.info["memory_info"] else 0
        except psutil.Error:
            continue
    cores = psutil.cpu_count() or 1
    top = sorted(usage.values(), key=lambda a: (a["cpu"], a["mem"]), reverse=True)[:limit]
    return [{"name": a["name"], "cpu": round(a["cpu"] / cores, 1), "mem": a["mem"]} for a in top]


apps()  # prime the per-process CPU counters


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
        "cpu": {"name": CPU_NAME, "pct": round(psutil.cpu_percent(None)), "cores": psutil.cpu_count(logical=False),
                "threads": psutil.cpu_count(), "ghz": round(freq.current / 1000, 2) if freq else None,
                "per_core": [round(x) for x in psutil.cpu_percent(None, percpu=True)]},
        "ram": {"used": vm.used, "total": vm.total, "pct": round(vm.percent)},
        "gpus": gpus(),
        "disks": disks(),
        "net": network(),
        "apps": apps(),
    }

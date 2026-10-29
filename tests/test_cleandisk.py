"""/cleandisk on throwaway folders: what gets deleted, and everything that must never be."""
import _winapi
import os
import time
from pathlib import Path

import pytest

from jarvis import cleandisk


@pytest.fixture
def home(tmp_path, monkeypatch):
    local, roaming = tmp_path / "AppData/Local", tmp_path / "AppData/Roaming"
    local.mkdir(parents=True)
    roaming.mkdir(parents=True)
    for name, value in (("HOME", tmp_path), ("LOCAL", local), ("ROAMING", roaming), ("WINDIR", tmp_path / "Windows")):
        monkeypatch.setattr(cleandisk, name, value)
    return tmp_path


def _file(p: Path, size=1000, age=0):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)
    if age:
        t = time.time() - age
        os.utime(p, (t, t))
    return p


def test_a_cache_is_emptied_but_the_folder_stays(home):
    cache = home / "AppData/Local/SomeApp/Cache"
    _file(cache / "a.bin")
    _file(cache / "sub/b.bin", 500)
    out = cleandisk.clean([{"id": "x", "label": "x", "verdict": "safe", "roots": [str(cache)]}])
    assert out["freed"] == 1500
    assert cache.is_dir() and not any(cache.iterdir())


def test_temp_keeps_recent_files(home):
    temp = home / "AppData/Local/Temp"
    old, new = _file(temp / "old.tmp", age=3 * 86400), _file(temp / "new.tmp")
    cleandisk.clean([{"id": "temp", "label": "t", "verdict": "safe", "roots": [str(temp)], "older": 86400}])
    assert not old.exists() and new.exists()


def test_a_junction_inside_a_cache_is_never_followed(home, tmp_path):
    precious = _file(tmp_path / "MyFiles/thesis.docx")
    cache = home / "AppData/Local/SomeApp/Cache"
    cache.mkdir(parents=True)
    _winapi.CreateJunction(str(precious.parent), str(cache / "sneaky"))
    cleandisk.clean([{"id": "x", "label": "x", "verdict": "safe", "roots": [str(cache)]}])
    assert precious.exists()


@pytest.mark.parametrize("where", ["", "AppData", "AppData/Local", "MyFiles", "../outside"])
def test_folders_that_are_not_ours_are_refused(home, where):
    target = (home / where).resolve()
    keep = _file(target / "keep.txt")
    out = cleandisk.clean([{"id": "x", "label": "x", "verdict": "safe", "roots": [str(target)]}])
    assert keep.exists() and out["freed"] == 0


def test_leave_alone_is_never_cleaned_whatever_is_sent(home):
    cache = home / "AppData/Local/SomeApp/Cache"
    f = _file(cache / "a.bin")
    cleandisk.clean([{"id": "downloads", "label": "Downloads", "verdict": "leave", "roots": [str(cache)]}])
    assert f.exists()


def test_scan_sorts_into_the_three_groups(home, monkeypatch):
    """The folder list is fixed at import, so this scans the real caches (measuring only, nothing is deleted)."""
    monkeypatch.setattr(cleandisk, "_recycle", lambda d: (5 * 1024 * 1024, 3))
    items = cleandisk.scan(Path(home).drive)
    by = {i["id"]: i for i in items}
    assert by["recycle"]["verdict"] == "ask" and by["recycle"]["size"] == 5 * 1024 * 1024
    assert all(i["verdict"] in ("safe", "ask", "leave") for i in items)
    assert all(i["verdict"] == "leave" for i in items if i["id"] in ("hiberfil", "pagefile", "downloads", "documents"))
    assert all("roots" not in i or all(Path(r).is_dir() for r in i["roots"]) for i in items)

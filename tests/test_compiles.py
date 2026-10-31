"""Every module compiles, including the ones the other tests never import (a typo in __main__.py stops Jarvis from
starting at all, and nothing else would notice)."""
import py_compile
from pathlib import Path

import pytest

FILES = sorted((Path(__file__).resolve().parent.parent / "jarvis").glob("*.py"))


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_compiles(path):
    py_compile.compile(str(path), doraise=True)

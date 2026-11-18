"""Shkolo answers from an older reading say how old it is and why: Jarvis was logged out for 13 days and kept giving
the old average (3.80) as if it were today's."""
import time

import pytest

from jarvis import health, shkolo


@pytest.fixture
def old_reading(monkeypatch, tmp_path):
    monkeypatch.setattr(shkolo, "STATE", tmp_path / "shkolo.json")
    monkeypatch.setattr(shkolo, "_state", {"fetched": time.time() - 13 * 86400, "details": {}, "data": {
        "ok": True, "average": "3.80", "grades": [{"subject": "Математика", "term1": [{"id": "1", "value": "2"}],
                                                  "term2": [], "term1_final": "", "term2_final": ""}]}})
    monkeypatch.setattr(shkolo, "_run_reader", lambda: {"ok": False, "error": "logged_out"})
    monkeypatch.setattr(health, "problem", lambda *a, **k: None)


def test_an_old_average_says_its_old_and_that_jarvis_is_logged_out(old_reading):
    text = shkolo.grades_text("what's my average")
    first = text.splitlines()[0]
    assert "13 days ago" in first and "logged out of Shkolo" in first and "log in to Shkolo" in first
    assert "Average: 3.80" in text   # still there, but clearly marked


def test_in_bulgarian_too(old_reading):
    assert "отпреди 13 дни" in shkolo.grades_text("какъв ми е успехът").splitlines()[0]


def test_a_fresh_reading_has_no_note(monkeypatch, tmp_path):
    monkeypatch.setattr(shkolo, "STATE", tmp_path / "shkolo.json")
    monkeypatch.setattr(shkolo, "_state", {})
    monkeypatch.setattr(shkolo, "_run_reader", lambda: {"ok": True, "average": "4.50", "grades": [
        {"subject": "Математика", "term1": [{"id": "1", "value": "5"}], "term2": [], "term1_final": "", "term2_final": ""}]})
    monkeypatch.setattr(health, "fixed", lambda *a, **k: None)
    text = shkolo.grades_text("what's my average")
    assert text.startswith("Grades") and "Average: 4.50" in text

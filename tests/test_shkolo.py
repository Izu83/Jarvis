"""Shkolo: what Jarvis says about grades, homework, the timetable and absences, from a made-up diary."""
import datetime as dt

import pytest

from jarvis import nlu, shkolo

TUE = dt.date(2026, 11, 3)


def _diary():
    lesson = lambda n, s, t, room="101 (Етаж 1)": {"n": n, "subject": s, "teacher": "Учител", "room": room, "time": t}
    return {
        "ok": True, "average": "4.50", "absence_total": "3",
        "grades": [
            {"subject": "Математика", "term1": [{"id": "g1", "value": "6"}, {"id": "g2", "value": "3"}], "term1_final": "",
             "term2": [], "term2_final": "", "year_final": ""},
            {"subject": "Бази данни (ИУЧ - ОтПП)", "term1": [{"id": "g3", "value": "5"}], "term1_final": "5",
             "term2": [], "term2_final": "", "year_final": ""},
            {"subject": "Час на класа (…)", "term1": [], "term1_final": "", "term2": [], "term2_final": "", "year_final": ""},
        ],
        "homework": [{"subject": "Български език и литература", "teacher": "Учител", "date": "02.11.2026",
                      "text": "Стр. 36/ 9-11 зад.", "file": "", "due": "02.11.2026", "id": "h1"}],
        "timetable": [{"week": "45", "days": [
            {"day": "Понеделник", "date": "02.11.2026", "lessons": [], "note": "Есенна ваканция"},
            {"day": "Вторник", "date": "03.11.2026", "note": "", "lessons": [
                lesson("1", "Мат", "08:00 - 08:45", "404 (Етаж 4)"), lesson("2", "Мат", "09:05 - 09:50", "404 (Етаж 4)"),
                lesson("3", "Немски / Група 1", "10:00 - 10:45")]},
        ]}],
        "absences": [{"subject": "Математика", "late": "0", "unexcused": "2", "excused": "1"}],
    }


@pytest.fixture
def diary(monkeypatch):
    data = _diary()
    monkeypatch.setattr(shkolo, "read", lambda force=False: data)
    monkeypatch.setattr(shkolo, "enabled", lambda: True)
    monkeypatch.setattr(shkolo, "_state", {"data": data, "details": {
        "g2": {"date": "02.11.2026", "type": "Текуща", "teacher": "Учител"},
        "g1": {"date": "10.10.2026", "type": "Входно равнище", "teacher": "Учител"}}})
    monkeypatch.setattr(shkolo, "_save", lambda: None)
    return data


def test_grades_in_the_language_you_asked(diary):
    bg = shkolo.grades_text("какви оценки имам")
    assert "Математика: 6, 3" in bg and "Бази данни: 5 (срочна 5)" in bg and "Среден успех: 4.50" in bg
    assert "Час на класа" not in bg   # no grades there
    assert "Последна: 3 по Математика (Текуща, 02.11)" in bg
    assert "Latest: 3 in Математика" in shkolo.grades_text("my grades")
    assert shkolo.grades_text("x", "Мат").count("\n") == 1   # one subject


def test_timetable_merges_double_lessons_and_knows_holidays(diary):
    text = shkolo.timetable_text("какви часове имам", TUE)
    assert "3 часа, 08:00–10:45" in text and "1–2. Мат (08:00–09:50, 404)" in text and "3. Немски (10:00–10:45, 101)" in text
    assert "Есенна ваканция" in shkolo.timetable_text("програмата", dt.date(2026, 11, 2))
    assert "weekend" in shkolo.timetable_text("my timetable", dt.date(2026, 11, 7))
    assert shkolo.briefing_line(TUE) == "🏫 School 08:00–10:45: Мат, Немски."


def test_absences_and_homework(diary, monkeypatch):
    assert "Неизвинени: Математика 2" in shkolo.absences_text("отсъствия")

    class Day(dt.date):
        @classmethod
        def today(cls):
            return TUE
    monkeypatch.setattr(shkolo.dt, "date", Day)
    assert "02.11 · Български език и литература: Стр. 36/ 9-11 зад." in shkolo.homework_text("домашни")


def test_news_learns_first_then_tells(diary, monkeypatch):
    assert shkolo.news() == []   # the first look only learns what's there
    diary["grades"][0]["term1"].append({"id": "g9", "value": "6"})
    shkolo._state["details"]["g9"] = {"date": "04.11.2026", "type": "Контролна", "teacher": "Учител"}
    assert shkolo.news() == ["📚 Нова оценка: 6 по Математика (Контролна, 04.11)"]
    assert shkolo.news() == []


def test_quick_questions_reach_the_diary(diary):
    assert nlu.try_handle("какви оценки имам").startswith("Оценки, I срок")
    assert "Homework" in nlu.try_handle("what homework do i have")
    assert nlu.try_handle("колко отсъствия имам").startswith("Отсъствия: 3")
    assert nlu.try_handle("send my grades to mom") is None or "Оценки" not in nlu.try_handle("send my grades to mom")

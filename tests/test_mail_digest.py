"""New email: one summary at 22:00, not a message every time one arrives (proactive.py)."""
import datetime as dt

import pytest

from jarvis import proactive


@pytest.fixture
def inbox(monkeypatch):
    sent, box = [], []
    monkeypatch.setattr(proactive, "_state", {"mail_seeded": True, "seen_mail": []})
    monkeypatch.setattr(proactive, "_save_state", lambda: None)
    monkeypatch.setattr(proactive, "_sender", lambda text, chat, notify: sent.append(text))
    monkeypatch.setitem(proactive.settings, "email_digest", True)
    monkeypatch.setitem(proactive.settings, "email_address", "me@example.com")
    import jarvis.mail
    monkeypatch.setattr(jarvis.mail, "check", lambda unread_only=True, limit=10: list(box))
    return sent, box


def mail(i, important, frm="Teacher <t@school.bg>", subject="Homework", why=None):
    why = why or ("Gmail marked it important" if important else "newsletter or automated sender")
    return {"id": str(i), "from": frm, "subject": subject, "snippet": "Please read chapter 3", "important": important,
            "why": why, "body": "Please read chapter 3 by Friday."}


def test_new_email_waits_for_the_evening_summary(inbox):
    sent, box = inbox
    box += [mail(1, True, why="from a person"), mail(2, False, "Spotify <no-reply@spotify.com>", "New releases")]
    box[0]["important"] = True
    proactive._check_mail()
    assert sent == []   # nothing during the day: "from a person" alone isn't urgent
    text = proactive.mail_digest()
    assert text.startswith("📬 Today's email: 2 new.")
    assert "Teacher (Homework)" in text
    assert "Spotify (New releases)" in text
    assert proactive._state["mail_today"] == []   # tomorrow starts empty


def test_the_same_email_is_listed_once(inbox):
    sent, box = inbox
    box.append(mail(1, True))
    proactive._check_mail()
    proactive._check_mail()
    assert proactive.mail_digest().count("Homework") == 1


def test_a_quiet_day_is_one_line(inbox):
    assert proactive.mail_digest() == "📬 No new email today."


def test_it_is_due_at_ten_once(inbox, monkeypatch):
    monkeypatch.setitem(proactive.settings, "email_digest_time", "22:00")
    assert not proactive._digest_due(dt.datetime(2026, 11, 18, 21, 59))
    assert proactive._digest_due(dt.datetime(2026, 11, 18, 22, 0))
    proactive._state["mail_digest"] = "2026-11-18"
    assert not proactive._digest_due(dt.datetime(2026, 11, 18, 22, 30))


def test_switched_off_it_is_a_heads_up_again(inbox, monkeypatch):
    sent, box = inbox
    monkeypatch.setitem(proactive.settings, "email_digest", False)
    box.append(mail(1, True))
    proactive._check_mail()
    assert sent and sent[0].startswith("📬 New email from Teacher")


def test_an_important_one_comes_now_saying_what_its_about(inbox, monkeypatch):
    sent, box = inbox
    from jarvis import cloud
    asked = []
    monkeypatch.setattr(cloud, "quick", lambda prompt, providers=("groq", "nvidia"), **k: asked.append(providers) or
                        "Read chapter 3 by Friday.")
    box.append(mail(1, True, subject="Exam on Friday", why="looks time-sensitive"))
    proactive._check_mail()
    assert sent == ["📬 Important email from Teacher: Exam on Friday\nRead chapter 3 by Friday."]
    assert asked == [("groq", "nvidia")]   # only those read your email
    assert "• Teacher: Exam on Friday" in proactive.mail_digest()   # and it's in the evening summary too


def test_without_groq_its_the_first_lines(inbox, monkeypatch):
    sent, box = inbox
    from jarvis import cloud
    monkeypatch.setattr(cloud, "quick", lambda *a, **k: None)
    box.append(mail(1, True))
    proactive._check_mail()
    assert sent[0].endswith("\nPlease read chapter 3")


def test_ten_pm_is_your_ten_pm(inbox):
    proactive.set_phone_tz("Asia/Shanghai")
    import datetime as dt
    from zoneinfo import ZoneInfo
    assert abs((proactive.your_now() - dt.datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)).total_seconds()) < 5
    proactive.set_phone_tz("Not/AZone")
    assert proactive._state["phone_tz"] == "Asia/Shanghai"

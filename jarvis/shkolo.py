"""Your Shkolo e-diary (app.shkolo.bg): grades, homework, the timetable and absences.

Jarvis reads it in his own hidden browser, logged in as you (you log in once yourself: "log in to Shkolo", or the
tray › Log in to a site for Jarvis). shkolo_read.js does the reading (about 10 seconds, no AI and no tokens) and
only ever reads; this keeps the result for 20 minutes, answers from it, and every couple of hours during the day
looks for anything new (a grade, homework, an absence) to message you about.
"""
import datetime as dt
import json
import logging
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from . import hidden_browser
from .config import ROOT, settings

log = logging.getLogger("jarvis.shkolo")

SCRIPT = Path(__file__).with_name("shkolo_read.js")
STATE = ROOT / ".brain" / "shkolo.json"
FRESH = 20 * 60           # answers come from a reading this recent
CHECK_EVERY = 2 * 3600    # looking for news during the day
LOGIN_URL = "https://app.shkolo.bg/"
_lock = threading.Lock()
_state: dict | None = None
DAYS_BG = ["Понеделник", "Вторник", "Сряда", "Четвъртък", "Петък", "Събота", "Неделя"]
DAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def enabled() -> bool:
    return bool(settings.get("shkolo", True)) and SCRIPT.exists() and hidden_browser.CHROME.exists() \
        and hidden_browser.MCP_CLI.exists()


def _load() -> dict:
    global _state
    if _state is None:
        try:
            _state = json.loads(STATE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _state = {}
    return _state


def _save() -> None:
    try:
        STATE.write_text(json.dumps(_load(), ensure_ascii=False), encoding="utf-8")
    except OSError:
        log.debug("couldn't save the Shkolo state", exc_info=True)


def _run_reader() -> dict:
    """One reading of the diary, straight from Shkolo."""
    state = _load()
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(list(state.get("details", {})), f)
        known = f.name
    try:
        for attempt in range(3):
            try:
                out = subprocess.run(["node", str(SCRIPT), str(hidden_browser.PROFILE), known], cwd=str(ROOT),
                                     capture_output=True, text=True, encoding="utf-8", timeout=150,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
                line = (out.stdout.strip().splitlines() or ["{}"])[-1]
                data = json.loads(line)
            except (subprocess.TimeoutExpired, ValueError, OSError) as e:
                data = {"ok": False, "error": "failed", "detail": str(e)[:200]}
            if data.get("error") != "busy":
                return data
            time.sleep(10)   # the hidden browser is busy with another website job: wait for it
        return data
    finally:
        Path(known).unlink(missing_ok=True)


def read(force: bool = False) -> dict:
    """The diary: from the last 20 minutes, or read now. On a failure the last good reading comes back too, with
    "error" set ("logged_out", "busy", "failed", ...)."""
    with _lock:
        state = _load()
        last = state.get("data") or {}
        if not force and last and time.time() - state.get("fetched", 0) < FRESH:
            return last
        data = _run_reader()
        if not data.get("ok"):
            log.info("Couldn't read Shkolo: %s %s", data.get("error"), data.get("detail", ""))
            if data.get("error") == "logged_out":
                from . import health
                health.problem("shkolo", "I'm logged out of Shkolo. Say \"log in to Shkolo\" (or tray › Log in to "
                               "a site for Jarvis) and log in at app.shkolo.bg, then close the window.")
            return {**last, "ok": False, "error": data.get("error", "failed"), "stale": bool(last)}
        state.setdefault("details", {}).update(data.pop("details", {}) or {})
        state["data"], state["fetched"] = data, time.time()
        _save()
        from . import health
        health.fixed("shkolo")
        return data


# ---------------------------------------------------------------- putting it into words

def _short(subject: str) -> str:
    """"Бази данни (ИУЧ - ОтПП)" -> "Бази данни"; "Немски език / Група 1" -> "Немски език"."""
    return re.sub(r"\s*(\([^)]*\)|/\s*Група \d+|\(…\))\s*", " ", subject).strip()


def _bg(question: str) -> bool:
    return bool(re.search(r"[а-яА-Я]", question or "")) or settings.get("voice_language") == "bg"


def _date(s: str) -> dt.date | None:
    try:
        return dt.datetime.strptime(s, "%d.%m.%Y").date()
    except (TypeError, ValueError):
        return None


def _problem(data: dict, bg: bool) -> str | None:
    err = data.get("error")
    if not err or data.get("stale"):
        return None
    if err == "logged_out":
        return ("Излязъл съм от Shkolo. Кажи „влез в Shkolo“ и влез в app.shkolo.bg." if bg else
                "I'm logged out of Shkolo. Say \"log in to Shkolo\" and log in at app.shkolo.bg.")
    if err == "busy":
        return ("Браузърът ми е зает с друга задача. Опитай пак след минута." if bg else
                "My browser is busy with another website job. Ask again in a minute.")
    return "Не можах да отворя Shkolo точно сега." if bg else "I couldn't open Shkolo just now."


def _term(data: dict) -> str:
    """Which term the grades are in: the second once it has any grades."""
    return "term2" if any(s.get("term2") for s in data.get("grades", [])) else "term1"


def grades_text(question: str = "", subject: str = "") -> str:
    data, bg = read(), _bg(question)
    if (p := _problem(data, bg)) or not data.get("grades"):
        return p or ("Още няма оценки." if bg else "No grades yet.")
    term = _term(data)
    details = _load().get("details", {})
    rows = []
    for s in data["grades"]:
        if subject and subject.lower() not in s["subject"].lower():
            continue
        values = [g["value"] for g in s.get(term, [])]
        final = s.get(term + "_final")
        if values or final:
            rows.append(f"{_short(s['subject'])}: {', '.join(values) or '–'}"
                        + (f" ({'срочна' if bg else 'term grade'} {final})" if final else ""))
    if not rows:
        return (f"Няма оценки по {subject}." if bg else f"No grades in {subject} yet.") if subject else \
            ("Още няма оценки този срок." if bg else "No grades this term yet.")
    head = ("Оценки, " + ("II срок" if term == "term2" else "I срок") if bg else
            "Grades, " + ("second term" if term == "term2" else "first term"))
    text = head + ":\n" + "\n".join(rows)
    if data.get("average") and not subject:
        text += f"\n{'Среден успех' if bg else 'Average'}: {data['average']}."
    newest = _newest_grade(data, details)
    if newest and not subject:
        g, s, d = newest
        text += f"\nПоследна: {g['value']} по {_short(s)}" if bg else f"\nLatest: {g['value']} in {_short(s)}"
        if d.get("date"):
            text += f" ({', '.join(x for x in (d.get('type'), d['date'][:5]) if x)})"
    return text


def _newest_grade(data: dict, details: dict):
    best = None
    for s in data.get("grades", []):
        for g in s.get("term1", []) + s.get("term2", []):
            d = details.get(g["id"], {})
            when = _date(d.get("date", "")) or dt.date.min
            if best is None or when > best[0]:
                best = (when, g, s["subject"], d)
    return best[1:] if best else None


def homework_text(question: str = "", days: int = 21) -> str:
    data, bg = read(), _bg(question)
    if p := _problem(data, bg):
        return p
    since = dt.date.today() - dt.timedelta(days=days)
    items = sorted((h for h in data.get("homework", []) if (_date(h["date"]) or dt.date.min) >= since),
                   key=lambda h: _date(h["date"]) or dt.date.min, reverse=True)
    if not items:
        return ("Няма домашни от последните три седмици." if bg else "No homework in the last three weeks.")
    head = "Домашни:" if bg else "Homework:"
    return head + "\n" + "\n".join(
        f"{h['date'][:5]} · {_short(h['subject'])}: {h['text']}"
        + (f" ({'срок' if bg else 'due'} {h['due'][:5]})" if h.get("due") and h["due"] != h["date"] else "")
        for h in items[:10])


def _day(data: dict, day: dt.date) -> dict | None:
    for week in data.get("timetable", []):
        for d in week.get("days", []):
            if _date(d.get("date")) == day:
                return d
    return None


def _lessons(d: dict, bg: bool) -> str:
    """"2–3. УП по ВС (09:05–10:45, 223)" for the same lesson twice in a row."""
    out, i, ls = [], 0, d.get("lessons", [])
    while i < len(ls):
        j = i
        while j + 1 < len(ls) and ls[j + 1]["subject"] == ls[i]["subject"]:
            j += 1
        first, last = ls[i], ls[j]
        nums = first["n"] if i == j else f"{first['n']}–{last['n']}"
        start, end = first["time"].split(" - ")[0], last["time"].split(" - ")[-1]
        room = re.sub(r"\s*\(.*\)", "", first.get("room", ""))
        out.append(f"{nums}. {_short(first['subject'])} ({start}–{end}{', ' + room if room else ''})")
        i = j + 1
    return "\n".join(out)


def timetable_text(question: str = "", day: dt.date | None = None) -> str:
    data, bg = read(), _bg(question)
    if p := _problem(data, bg):
        return p
    day = day or dt.date.today()
    names = DAYS_BG if bg else DAYS_EN
    when = (("днес" if bg else "today") if day == dt.date.today() else
            ("утре" if bg else "tomorrow") if day == dt.date.today() + dt.timedelta(days=1) else
            (names[day.weekday()].lower() if bg else names[day.weekday()]) + f" {day:%d.%m}")
    d = _day(data, day)
    if not d or not d.get("lessons"):
        why = (d or {}).get("note") or ""
        if day.weekday() >= 5 and not why:
            return f"Няма училище {when}, почивен ден е." if bg else f"No school {when}, it's the weekend."
        return (f"Няма часове {when}" if bg else f"No lessons {when}") + (f": {why}." if why else ".")
    ls = d["lessons"]
    start, end = ls[0]["time"].split(" - ")[0], ls[-1]["time"].split(" - ")[-1]
    head = (f"Програма за {when}: {len(ls)} часа, {start}–{end}." if bg else
            f"Timetable {when}: {len(ls)} lessons, {start}–{end}.")
    return head + "\n" + _lessons(d, bg)


def absences_text(question: str = "") -> str:
    data, bg = read(), _bg(question)
    if p := _problem(data, bg):
        return p
    rows = data.get("absences", [])
    unexcused = [r for r in rows if r.get("unexcused") not in ("", "0")]
    total = data.get("absence_total", "")
    text = (f"Отсъствия: {total} общо." if bg else f"Absences: {total} in total.") if total else \
        ("Отсъствия:" if bg else "Absences:")
    if unexcused:
        text += ("\nНеизвинени: " if bg else "\nUnexcused: ") + ", ".join(
            f"{_short(r['subject'])} {r['unexcused']}" for r in unexcused)
    else:
        text += " Няма неизвинени." if bg else " None unexcused."
    return text


def tool(args: dict) -> str:
    """The "school" tool, for whichever brain answers: the diary as text."""
    if not enabled():
        return "Shkolo isn't available on this PC (it needs Google Chrome and the hidden browser)."
    what = args.get("what", "everything")
    day = None
    if args.get("day"):
        raw = str(args["day"]).strip().lower()
        day = dt.date.today() + dt.timedelta(days=1) if raw in ("tomorrow", "утре") else \
            dt.date.today() if raw in ("today", "днес") else _date(raw) or _iso(raw)
    parts = {
        "grades": lambda: grades_text("", args.get("subject", "")),
        "homework": lambda: homework_text(""),
        "timetable": lambda: timetable_text("", day),
        "absences": lambda: absences_text(""),
    }
    if what in parts:
        return parts[what]()
    return "\n\n".join(f() for f in parts.values())


def _iso(s: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        return None


# ---------------------------------------------------------------- the briefing, the recap and the news

def briefing_line(day: dt.date) -> str | None:
    """For the morning briefing: today's lessons, from the last reading (read now if it's old)."""
    if not enabled() or not _load().get("data"):
        return None
    data = read()
    d = _day(data, day)
    if not d:
        return None
    if not d.get("lessons"):
        return f"🏫 No school today: {d['note']}." if d.get("note") else None
    ls = d["lessons"]
    subjects = []
    for l in ls:
        name = _short(l["subject"])
        if name not in subjects:
            subjects.append(name)
    return (f"🏫 School {ls[0]['time'].split(' - ')[0]}–{ls[-1]['time'].split(' - ')[-1]}: "
            + ", ".join(subjects) + ".")


def tomorrow_line(day: dt.date) -> str | None:
    """For the evening recap: tomorrow's lessons, so you can pack your bag."""
    return (briefing_line(day) or "").replace("🏫 School", "🏫 Tomorrow at school").replace(
        "🏫 No school today", "🏫 No school tomorrow") or None


def news() -> list[str]:
    """Anything new since the last look: grades, homework, absences. The first look only learns what's there."""
    data = read(force=True)
    if not data.get("ok"):
        return []
    state = _load()
    seen = state.setdefault("seen", {})
    first = not seen
    grades = {g["id"]: (g["value"], s["subject"]) for s in data.get("grades", [])
              for g in s.get("term1", []) + s.get("term2", [])}
    homework = {h["id"] or h["date"] + h["text"]: h for h in data.get("homework", [])}
    out = []
    if not first:
        details = state.get("details", {})
        for gid, (value, subject) in grades.items():
            if gid not in seen.get("grades", []):
                d = details.get(gid, {})
                extra = ", ".join(x for x in (d.get("type"), d.get("date", "")[:5]) if x)
                out.append(f"📚 Нова оценка: {value} по {_short(subject)}" + (f" ({extra})" if extra else ""))
        for hid, h in homework.items():
            if hid not in seen.get("homework", []):
                out.append(f"📝 Ново домашно по {_short(h['subject'])}: {h['text']}")
        if data.get("absence_total") and seen.get("absences") and data["absence_total"] != seen["absences"]:
            out.append(f"🕒 Отсъствията ти вече са {data['absence_total']} (бяха {seen['absences']}).")
    state["seen"] = {"grades": list(grades), "homework": list(homework), "absences": data.get("absence_total", "")}
    _save()
    return out


def check_due(now: dt.datetime) -> bool:
    """Time to look for news: every couple of hours from 7:00 to 21:00, once logged in."""
    if not enabled() or not settings.get("shkolo_alerts", True) or not _load().get("data"):
        return False
    return 7 <= now.hour < 21 and time.time() - _load().get("checked", 0) >= CHECK_EVERY


def mark_checked() -> None:
    _load()["checked"] = time.time()
    _save()

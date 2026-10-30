"""The evening recap: one message about your day and tomorrow, put together on the PC (no Claude, no tokens).

- What Jarvis did for you today (from the activity log).
- Your time in apps on the PC (apptime.py).
- Tomorrow: the first things in your calendar, reminders, and the weather.

Sent every evening at "recap_time" while "recap" is on (proactive.py does the timing), or any time with /recap or
"how was my day". Each part that has nothing to say is left out.
"""
import datetime as dt
import re

from . import activity, apptime, briefing


def _short(text: str, n: int = 38) -> str:
    text = re.sub(r"\s+", " ", text).strip().rstrip(".?!")
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0].rstrip(" ,") + "…"   # at a word


def _did(day: dt.date) -> str | None:
    start = dt.datetime.combine(day, dt.time()).timestamp() * 1000
    done = [e for e in activity.summary(200) if e["kind"] == "command" and e["started"] >= start
            and e["status"] == "done"]
    if not done:
        return "🤖 A quiet day: you didn't need me once."
    seen, examples = set(), []
    for e in reversed(done):   # oldest first
        t = _short(e["text"])
        if t.lower() not in seen:
            seen.add(t.lower())
            examples.append(f"“{t}”")
    examples = examples[:3]
    like = examples[0] if len(examples) == 1 else ", ".join(examples[:-1]) + " and " + examples[-1]
    n = len(done)
    return f"🤖 I handled {n} thing{'s' if n != 1 else ''} for you today, like {like}."


def _apps(day: dt.date) -> str | None:
    used = apptime.today(day)
    total = sum(m for _, m in used)
    if total < 5:
        return None
    top = [f"{app} {apptime.duration(m)}" for app, m in used[:3] if m >= 5]
    if not top:
        return None
    listed = top[0] if len(top) == 1 else ", ".join(top[:-1]) + " and " + top[-1]
    return f"⏱ On your PC: {listed} ({apptime.duration(total)} in all)."


def _tomorrow(day: dt.date) -> str | None:
    things = (briefing.events(day) or [])[:3] + briefing.reminders(day)[:2]
    return "📅 Tomorrow: " + "; ".join(things) + "." if things else None


def compose(now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now()
    today, tomorrow = now.date(), now.date() + dt.timedelta(days=1)
    lines = [f"🌙 Your {now:%A}, {now.day} {now:%B}."]
    for part in (_did(today), _apps(today), _tomorrow(tomorrow), briefing.forecast(1)):
        if part:
            lines.append(part)
    lines.append("Tomorrow's the weekend. Sleep in." if tomorrow.weekday() == 5 else "Good night.")
    return "\n".join(lines)

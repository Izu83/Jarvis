"""A bit of wit for the replies that never reach a brain (volume, skip, lock, the morning briefing…).

They're canned, so they cost no tokens. How often one is used follows the humour setting: "off" never, "light"
now and then, "lots" most of the time. The same line never comes twice in a row, and callers only ask for one when
the action worked: a failure gets a plain answer.
"""
import random

from .config import settings

CHANCE = {"off": 0.0, "light": 0.25, "lots": 0.6}

LINES = {
    "volume_up": ["Louder. The neighbours have been informed, in a manner of speaking.",
                  "Turned up. Your ears, your call.",
                  "Volume up. Let's give the speakers something to do."],
    "volume_down": ["Turned down. Civilisation restored.",
                    "Quieter. The walls thank you.",
                    "Volume down. Very considerate of you."],
    "mute": ["Muted. Blissful silence.",
             "Muted. I'll pretend I didn't hear that."],
    "skip": ["Skipped. That one had it coming.",
             "Next. We shall never speak of that song again.",
             "Skipped. Not every song can be a winner."],
    "previous": ["Back one. Some songs deserve an encore.",
                 "Went back. Couldn't resist, could you?"],
    "pause": ["Paused. The music will wait for you. It has nowhere else to be.",
              "Paused. Take your time."],
    "resume": ["Resumed. And we're back.",
               "Playing again. Where were we?"],
    "lock": ["Locked. Nobody gets in on my watch.",
             "Locked. Your secrets are safe with me. Mostly because I can't read them."],
    "sent_file": ["There you go. Found it on the first try, unlike some search engines I could mention.",
                  "Sent. Fetched faster than a golden retriever, and with less drool."],
    # the briefing's last line
    "morning": ["Go get them.",
                "Coffee first. Then world domination.",
                "Try not to open forty browser tabs before lunch.",
                "I'll hold the fort here.",
                "Make it a good one. I'll be right here, being a computer."],
    "monday": ["It's Monday. I'm as sorry as you are.",
               "Monday again. They do keep coming back."],
    "friday": ["It's Friday. You can see the weekend from here.",
               "Friday. Nearly there. Pace yourself."],
    "weekend": ["It's the weekend. I'll try not to bother you. Much.",
                "Weekend. The PC and I will be here if you need us."],
}

_last: dict[str, str] = {}


def pick(key: str) -> str:
    """A line from LINES[key], never the one used last time."""
    options = [s for s in LINES.get(key, []) if s != _last.get(key)] or LINES.get(key, [""])
    line = random.choice(options)
    _last[key] = line
    return line


def flavour(key: str, plain: str) -> str:
    """Now and then (as often as the humour setting says), a witty version of a plain reply instead of it."""
    if key not in LINES or random.random() >= CHANCE.get(settings.get("humour", "light"), 0.0):
        return plain
    return pick(key)


def signoff(weekday: int) -> str:
    """The briefing's last line: "" with humour off, otherwise something for the day (0 = Monday)."""
    if settings.get("humour", "light") == "off":
        return ""
    if weekday == 0:
        return pick("monday")
    if weekday == 4:
        return pick("friday")
    if weekday >= 5:
        return pick("weekend")
    return pick("morning")

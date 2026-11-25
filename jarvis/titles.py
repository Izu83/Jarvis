"""Short names for new chats. The app used to name a chat after the start of your first message ("Yo Jarv, so now i
have ChatG…"); after Jarvis's first answer it now asks for a name of a few words instead ("ChatGPT Go and Codex").
Only Groq: it answers in about a second, for free, and doesn't train on what it's sent. NVIDIA's free models were
tried and are too slow for this (or think until they run out of room). Without Groq the app's own rule names the
chat (shortTitle in index.html), so nothing is lost."""
import re

PROMPT = ("Give this conversation a short title: 2 to 5 words, in the language the user wrote in, like a chat title "
          "in a messaging app. No quotes, no full stop, no emoji. Reply with the title only.\n\n"
          "User: {user}\n\nAssistant: {reply}")


def clean(text: str | None) -> str:
    """The model's answer as a title, or "" when it isn't one."""
    if not text:
        return ""
    line = next((l for l in text.strip().splitlines() if l.strip()), "")
    line = re.sub(r"^\s*(title|заглавие)\s*:\s*", "", line, flags=re.I)
    line = line.strip().strip("\"'“”„«»*`#").strip().rstrip(".!。").strip()
    if not line or len(line) > 60 or len(line.split()) > 8:
        return ""
    return line[0].upper() + line[1:]


def title_for(user: str, reply: str) -> str:
    from . import cloud
    said = cloud.quick(PROMPT.format(user=user[:600], reply=reply[:800]), providers=("groq",), max_tokens=24)
    return clean(said)

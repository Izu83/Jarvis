"""Which model gets a command: the best one for the job that costs the least.

From the cheapest up: the local models (free, on your graphics card, but slow and weaker), then your plans (Claude
Pro through Claude Code, Gemini Pro through the Gemini CLI: no extra cost, but each has limits), then pay-per-use
APIs (ChatGPT, Grok: only with a key, ticked, and under the monthly budget). Among your plans, Gemini takes what
it's best at and the everyday questions, so your Claude limit lasts for what Claude is best at.

    doing things on the PC, many steps     Claude    -> Gemini -> local -> ChatGPT
    code                                    Claude    -> ChatGPT -> Gemini
    seeing the screen, photos you send      Claude    -> Gemini -> ChatGPT
    current things (news, prices, search)   Gemini    -> Claude -> Grok -> ChatGPT
    YouTube, long documents                 Gemini    -> Claude
    X / Twitter                             Grok      -> Gemini -> Claude
    everyday questions and chat             Gemini    -> Claude -> local -> ChatGPT -> Grok

Whoever is switched off in the model menu (or not set up) is skipped, and the next in line answers when one fails or
hits its limit. All of it is plain rules on the PC: picking costs nothing.
"""
import re

ACTION = re.compile(
    r"\b(open|close|quit|launch|start|stop|move|put|arrange|snap|minimi[sz]e|maximi[sz]e|play|pause|skip|mute|unmute|"
    r"volume|send|email|e-mail|reply|forward|text|message|upload|download|delete|remove|rename|copy|install|uninstall|"
    r"click|type|press|scroll|screenshot|record|lock|shut ?down|restart|sleep|turn (on|off)|switch|set|change|create|"
    r"make|organi[sz]e|clean|sort|find (my|the|a) (file|folder|pdf|photo|video)|folder|file|desktop|monitor|window|"
    r"tab|browser|spotify|chrome|opera|zen|discord|steam|remind me|schedule|every (day|morning|evening|hour))\b", re.I)
CODE = re.compile(r"\b(code|script|python|javascript|typescript|html|css|regex|bug|debug|function|compile|program|"
                  r"sql|api|json|stack ?trace|error in)\b", re.I)
SIGHT = re.compile(r"(\bscreen\b|\blook at\b|what do you see|\bphoto\b|\bpicture\b|\bimage\b|"
                   r"\[the user attached these files)", re.I)
CURRENT = re.compile(r"\b(news|latest|today'?s|this week|right now|currently|price|cost of|how much is|stock|crypto|"
                     r"bitcoin|score|who won|results?|release date|weather in|search|look up|google|online|on the web|"
                     r"what'?s new|trending|recent|update on|2025|2026|2027)\b", re.I)
LONG = re.compile(r"(youtube\.com|youtu\.be|\bvideo\b|\bpdf\b|\bdocument\b|\bsummari[sz]e\b|\btranscript\b)", re.I)
X = re.compile(r"(\btwitter\b|\btweets?\b|\bx\.com\b|on x\b|\belon\b|\bgrok\b)", re.I)


def kind(command: str) -> str:
    if "[the user attached these files" in command.lower():   # photos and documents: a model that can see them
        return "sight"
    c = command.split("\n\n[")[0]   # what the user said, not the notes added to it (files, where they are)
    if X.search(c):
        return "x"
    if SIGHT.search(c):
        return "sight"
    if CODE.search(c) and not CURRENT.search(c):
        return "code"
    if ACTION.search(c):
        return "action"
    if LONG.search(c):
        return "long"
    if CURRENT.search(c):
        return "current"
    return "chat"


ORDER = {
    "action":  ["claude", "gemini", "local", "chatgpt"],
    "code":    ["claude", "chatgpt", "gemini", "local"],
    "sight":   ["claude", "gemini", "chatgpt"],
    "current": ["gemini", "claude", "grok", "chatgpt"],
    "long":    ["gemini", "claude", "chatgpt"],
    "x":       ["grok", "gemini", "claude", "chatgpt"],
    "chat":    ["gemini", "claude", "local", "chatgpt", "grok"],
}


def plan(command: str, available: set[str], local_first: bool = False) -> list[str]:
    """The models to try for this command, in order, from the ones available."""
    k = kind(command)
    order = list(ORDER[k])
    if local_first and "local" in order and k not in ("sight", "current", "long", "x"):
        order.remove("local")
        order.insert(0, "local")   # "Try local models first" (Settings): free first, for what they can do
    return [m for m in order if m in available]

"""Which model gets a command: the best one for the job that costs the least.

From the cheapest up: the local models (free, on your graphics card, but slow and weaker), then your plans (Claude
Pro through Claude Code, Gemini Pro through the Gemini CLI: no extra cost, but each has limits), then pay-per-use
APIs (ChatGPT, Grok: only with a key, ticked, and under the monthly budget). Among your plans, Gemini takes what
it's best at and the everyday questions, so your Claude limit lasts for what Claude is best at.

    doing things on the PC, many steps     Claude    -> Gemini -> free models -> local -> ChatGPT
    code                                    Claude    -> ChatGPT -> Gemini -> free models
    seeing the screen, photos you send      Claude    -> Gemini -> ChatGPT
    current things (news, prices, search)   Gemini    -> Claude -> Grok -> ChatGPT -> free models
    YouTube, long documents                 Gemini    -> Claude -> free models
    X / Twitter                             Grok      -> Gemini -> Claude
    everyday questions and chat             Gemini    -> free models -> Claude -> local -> ChatGPT -> Grok

("Free models" are OpenRouter's: DeepSeek whenever it's free there, otherwise the strongest free ones.)

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


PLEASANTRY = re.compile(r"^\W*(thanks?( you)?|thx|ty|ok(ay)?|cool|nice|great|perfect|lol|haha|good( job)?|"
                        r"awesome|wow|yes|yep|no|nope)\W*$", re.I)
TASKS = ("action", "code", "sight")


def kind(command: str, previous: str | None = None) -> str:
    """What sort of job this is. previous: the last command's kind in this conversation, if it was recent. A
    follow-up with nothing to go on ("okay but make it REALLY blurred", "go again") carries on that job, so the
    model that can do it gets it instead of the one for small talk."""
    k = _kind(command)
    if k == "chat" and previous in TASKS and not PLEASANTRY.match(command.split("\n\n[")[0]):
        return previous
    return k


def _kind(command: str) -> str:
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


ORDER = {   # "openrouter" = OpenRouter's free models (DeepSeek when it's free there): text only, no web search
    "action":  ["claude", "gemini", "openrouter", "local", "chatgpt"],
    "code":    ["claude", "chatgpt", "gemini", "openrouter", "local"],
    "sight":   ["claude", "gemini", "chatgpt"],
    "current": ["gemini", "claude", "grok", "chatgpt", "openrouter"],
    "long":    ["gemini", "claude", "openrouter", "chatgpt"],
    "x":       ["grok", "gemini", "claude", "chatgpt"],
    "chat":    ["gemini", "openrouter", "claude", "local", "chatgpt", "grok"],
}


def plan(command: str, available: set[str], local_first: bool = False, k: str | None = None) -> list[str]:
    """The models to try for this command, in order, from the ones available (k: its kind, if already known)."""
    k = k or kind(command)
    order = list(ORDER[k])
    if local_first and "local" in order and k not in ("sight", "current", "long", "x"):
        order.remove("local")
        order.insert(0, "local")   # "Try local models first" (Settings): free first, for what they can do
    return [m for m in order if m in available]

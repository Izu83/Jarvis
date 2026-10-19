"""Run Jarvis:  python -m jarvis [--text] [--no-voice] [--no-phone] [--pair]"""
import argparse
import logging
import os
import queue
import sys
import threading
import time

try:
    # Must load before the Windows media library (album covers): that library ships an older msvcp140.dll,
    # and if it gets loaded first, onnxruntime (the wake word) crashes the whole process on import.
    import onnxruntime  # noqa: F401
except ImportError:
    pass

from .windows import enable_dpi_awareness  # noqa: E402

enable_dpi_awareness()  # before anything touches windows

from . import modes, nlu, speak  # noqa: E402
from .config import ROOT, secret, settings  # noqa: E402

log = logging.getLogger("jarvis")


def pair() -> None:
    import qrcode
    import qrcode.image.svg

    from .remote import pairing_link

    link = pairing_link()
    qr_path = ROOT / "pair-phone.svg"
    qrcode.make(link, image_factory=qrcode.image.svg.SvgPathFillImage).save(qr_path)
    qr = qrcode.QRCode(border=1)
    qr.add_data(link)
    qr.print_ascii(invert=True)
    print(f"\nScan the QR code above (also saved to {qr_path}) with your phone, or open this link on it:\n{link}\n")
    print("The link contains your private key: don't share it. Re-pair any time with: python -m jarvis --pair")
    if not settings["phone_page_url"]:
        print("\nNOTE: set \"phone_page_url\" in config.json to your GitHub Pages URL first, then run --pair again.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="jarvis")
    parser.add_argument("--text", action="store_true", help="type commands here instead of / as well as speaking")
    parser.add_argument("--no-voice", action="store_true", help="don't use the microphone")
    parser.add_argument("--no-phone", action="store_true", help="don't listen for phone commands")
    parser.add_argument("--pair", action="store_true", help="show the link/QR code that connects your phone")
    parser.add_argument("--startup", action="store_true", help="start Jarvis hidden at login from now on")
    parser.add_argument("--startup-off", action="store_true", help="stop starting Jarvis at login")
    args = parser.parse_args()

    if args.startup or args.startup_off:
        from . import autostart
        print(autostart.install() if args.startup else autostart.uninstall())
        return

    handlers = [logging.FileHandler(ROOT / "jarvis.log", encoding="utf-8")]
    if sys.stdout is not None:   # pythonw (hidden autostart) has no console
        handlers.insert(0, logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S",
                        handlers=handlers)
    for noisy in ("httpx", "httpx2", "anthropic", "faster_whisper", "comtypes", "websockets", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if args.pair:
        pair()
        return
    if settings["brain"] == "api" and not secret("ANTHROPIC_API_KEY"):
        log.warning("ANTHROPIC_API_KEY isn't set in .env; falling back to any `ant auth login` profile.")

    from . import approval
    from .brain import make_brain

    phone = None
    current = {"via": "pc"}   # where the command being worked on came from, so we ask there

    def ask_user(title: str, detail: str, confirm: str) -> bool:
        if current["via"] == "phone" and phone and phone.clients:
            log.info("Asking on the phone: %s", title)
            ok = phone.ask(title, detail, confirm)
        else:
            log.info("Asking on the PC: %s", title)
            speak.say("I need your permission. " + title)
            ok = approval.desktop_confirm(title, detail, confirm)
        log.info("Permission %s: %s", "granted" if ok else "refused", title)
        return ok

    _bridge = {"phone": None}

    def ring_phone(msg: str) -> str:
        from . import push
        ph = _bridge["phone"]
        sent = False
        if ph and ph.clients:
            ph.push({"kind": "call", "id": "call", "text": msg})
            sent = True
        if push.have_subs():   # also push it: an open-looking socket may belong to a phone that's asleep
            push.send_async("📞 Jarvis is calling…", msg or "Tap to answer.", "call", "./?call=1")
            sent = True
        return "Ringing your phone." if sent else ("Your phone isn't reachable: turn on notifications in the "
                                                   "Jarvis app's settings so I can reach it when it's closed.")

    def notify(title: str, body: str, call: bool = False) -> None:
        """A watch or reminder fired: always push to the phone, plus a banner if the app is open."""
        from . import push
        if call:
            ring_phone(body)
            return
        ph = _bridge["phone"]
        if ph and ph.clients:
            ph._alert("watch", "success", "check", title, body, also_push=False)
        if push.have_subs():
            push.send_async(title, body, "watch", "./")
        elif not (ph and ph.clients):
            log.warning("Couldn't deliver '%s': no phone has notifications turned on.", title)

    from . import watchers
    watchers.set_notifier(notify)
    watchers.load_reminders()

    def main_tool(name: str, args: dict) -> str:   # tools that must live in this process
        if name == "notify_when":
            return watchers.add(args["kind"], args["label"], args.get("name", ""), args.get("path", ""))
        if name == "cancel_watches":
            return watchers.clear()
        if name == "call_user":
            return ring_phone(args.get("message", ""))
        if name == "remind_me":
            return watchers.remind(args.get("message", ""), args.get("at", ""), args.get("in_minutes") or 0,
                                   bool(args.get("call")))
        if name == "list_watches":
            return watchers.listing()
        return "Unknown action."

    os.environ.update(approval.serve(
        ask_user, lambda payload: _bridge["phone"] and _bridge["phone"].push(payload), main_tool))
    brain = make_brain()
    speak.start()
    jobs: queue.Queue = queue.Queue()

    def submit(text: str, reply=None, chat=None, recent=None, progress=None, via: str = "pc") -> None:
        jobs.put((text, reply, chat, recent, progress, via))

    def worker() -> None:
        while True:
            text, reply, chat, recent, progress, via = jobs.get()
            current["via"] = via
            log.info("Command: %s", text)
            mode = modes.match(text)  # "work mode" etc. runs instantly, no Claude needed
            thinking = ""
            try:
                if mode:
                    answer = modes.run(mode)
                else:
                    answer = nlu.try_handle(text, progress=progress)  # common commands, no Claude, no tokens
                    if answer is not None:
                        log.info("(handled locally, no tokens used)")
                    elif settings["use_claude"]:                       # not understood locally -> Claude
                        answer = brain.handle(text, chat=chat, recent=recent, progress=progress)
                        thinking = getattr(brain, "last_thinking", "") or ""
                    else:                                              # Claude is switched off: stay free
                        answer = ("I didn't catch that one, and Claude is switched off to save tokens. "
                                  "Try a simpler command, or turn Claude back on in Settings.")
            except Exception as e:
                log.exception("Command failed")
                answer = f"Something went wrong: {e}"
            log.info("Jarvis: %s", answer)
            if reply:
                try:
                    reply(answer, thinking)
                except TypeError:   # older reply callbacks take only the text
                    reply(answer)
            else:
                speak.say(answer)

    threading.Thread(target=worker, daemon=True, name="worker").start()

    if settings["phone_enabled"] and not args.no_phone:
        from .remote import PhoneChannel
        phone = PhoneChannel(submit)
        _bridge["phone"] = phone   # let tools push files to the phone via the approval bridge
        threading.Thread(target=phone.run, daemon=True, name="phone").start()

    if settings["voice_enabled"] and not args.no_voice:
        from .voice import VoiceListener
        threading.Thread(target=VoiceListener(submit).run, daemon=True, name="voice").start()

    speak.say("Jarvis online.")
    try:
        if args.text:
            print("Type a command (Ctrl+C to quit).")
            while True:
                line = input("> ").strip()
                if line:
                    done = threading.Event()

                    def show(answer: str, thinking: str = "", done=done) -> None:
                        if thinking:
                            print(f"[thinking]\n{thinking}\n")
                        print(f"Jarvis: {answer}")
                        done.set()

                    submit(line, show)
                    done.wait()
        else:
            while True:
                time.sleep(1)
    except (KeyboardInterrupt, EOFError):
        print("\nJarvis offline.")


if __name__ == "__main__":
    main()

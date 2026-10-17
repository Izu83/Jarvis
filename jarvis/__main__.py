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

from . import modes, speak  # noqa: E402
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
    os.environ.update(approval.serve(ask_user, lambda payload: _bridge["phone"] and _bridge["phone"].push(payload)))
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
            try:
                answer = modes.run(mode) if mode else brain.handle(text, chat=chat, recent=recent, progress=progress)
            except Exception as e:
                log.exception("Command failed")
                answer = f"Something went wrong: {e}"
            log.info("Jarvis: %s", answer)
            if reply:
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

                    def show(answer: str, done=done) -> None:
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

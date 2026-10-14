"""Run Jarvis:  python -m jarvis [--text] [--no-voice] [--no-phone] [--pair]"""
import argparse
import logging
import queue
import sys
import threading
import time

from .windows import enable_dpi_awareness

enable_dpi_awareness()  # before anything touches windows

from . import speak  # noqa: E402
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
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(ROOT / "jarvis.log", encoding="utf-8")],
    )
    for noisy in ("httpx", "httpx2", "anthropic", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if args.pair:
        pair()
        return
    if settings["brain"] == "api" and not secret("ANTHROPIC_API_KEY"):
        log.warning("ANTHROPIC_API_KEY isn't set in .env; falling back to any `ant auth login` profile.")

    from .brain import make_brain

    brain = make_brain()
    speak.start()
    jobs: queue.Queue = queue.Queue()

    def submit(text: str, reply=None) -> None:
        jobs.put((text, reply))

    def worker() -> None:
        while True:
            text, reply = jobs.get()
            log.info("Command: %s", text)
            answer = brain.handle(text)
            log.info("Jarvis: %s", answer)
            if reply:
                reply(answer)
            else:
                speak.say(answer)

    threading.Thread(target=worker, daemon=True, name="worker").start()

    if settings["phone_enabled"] and not args.no_phone:
        from .remote import PhoneChannel
        threading.Thread(target=PhoneChannel(submit).run, daemon=True, name="phone").start()

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

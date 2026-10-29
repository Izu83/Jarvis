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

from . import activity, memory, modes, nlu, speak, stopping  # noqa: E402
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


def email_setup() -> None:
    """Connect Jarvis to an email account: asks for the address and an app password (typed hidden, saved in .env)."""
    import getpass
    import imaplib

    from . import mail
    from .config import set_secret, set_setting

    print("Jarvis signs in with an APP PASSWORD, never your normal password.")
    print("Gmail: turn on 2-Step Verification, then make one at https://myaccount.google.com/apppasswords\n")
    current = settings.get("email_address", "")
    address = input(f"Email address [{current}]: ").strip() or current
    if not address:
        print("No address given; nothing changed.")
        return
    password = getpass.getpass("App password (hidden as you type or paste): ").replace(" ", "").strip()
    if not password:
        print("No password given; nothing changed.")
        return
    try:
        imap = imaplib.IMAP4_SSL(mail._hosts(address)[0], timeout=mail.TIMEOUT)
        imap.login(address, password)
        imap.logout()
    except Exception as e:
        print(f"Couldn't sign in with that ({e}). Check the address and the app password, then try again.")
        return
    set_setting("email_address", address)
    set_secret("EMAIL_APP_PASSWORD", password)
    print(f"Signed in. Jarvis can now read and send email as {address}. Restart Jarvis to use it.")


_singleton_handle = None


def _already_running() -> bool:
    """True if another Jarvis already holds the single-instance lock (a Windows named mutex)."""
    global _singleton_handle
    try:
        import ctypes
        k = ctypes.windll.kernel32
        _singleton_handle = k.CreateMutexW(None, False, "JarvisSingleInstance_v1")  # keep the handle for life
        return k.GetLastError() == 183   # ERROR_ALREADY_EXISTS
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(prog="jarvis")
    parser.add_argument("--text", action="store_true", help="type commands here instead of / as well as speaking")
    parser.add_argument("--no-voice", action="store_true", help="don't use the microphone")
    parser.add_argument("--no-phone", action="store_true", help="don't listen for phone commands")
    parser.add_argument("--no-tray", action="store_true", help="don't show the taskbar tray icon")
    parser.add_argument("--pair", action="store_true", help="show the link/QR code that connects your phone")
    parser.add_argument("--email", action="store_true", help="connect your email account (app password)")
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
    if args.email:
        email_setup()
        return

    # Only one Jarvis at a time. A named mutex is held for the life of the process; a second copy (however it
    # got launched) sees it already exists and bows out, so two copies never answer the same command.
    if _already_running():
        log.warning("Jarvis is already running — this duplicate is exiting.")
        return

    if settings["brain"] == "api" and not secret("ANTHROPIC_API_KEY"):
        log.warning("ANTHROPIC_API_KEY isn't set in .env; falling back to any `ant auth login` profile.")

    from . import approval
    from .brain import make_brain

    phone = None
    current = {"via": "pc", "chat": None, "msg_id": ""}   # where the command being worked on came from

    def ask_user(title: str, detail: str, confirm: str) -> bool:
        if current["via"] in ("phone", "schedule", "siri") and phone and phone.clients:
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
        """A watch or reminder fired: always push to the phone, and put it in the chat as a message from Jarvis."""
        from . import push
        if call:
            ring_phone(body)
            return
        ph = _bridge["phone"]
        if ph:
            ph.message(f"{title}: {body}" if body else title, notify=False)
        if push.have_subs():   # straight away: a reminder must not wait to see if the app picks it up
            push.send_async(title, body, "watch", "./")
        elif not (ph and ph.clients):
            log.warning("Couldn't deliver '%s': no phone has notifications turned on.", title)

    def tell_user(text: str, chat=None, notify_too: bool = True) -> None:
        """A message from Jarvis that nobody prompted just now (a scheduled task's result, a new email)."""
        ph = _bridge["phone"]
        if ph:
            ph.message(text, chat, notify=notify_too)
        else:
            speak.say(text)

    def run_scheduled(task: str, chat=None) -> None:
        """A scheduled task (or the good-morning message) runs like a command; its answer arrives as a message."""
        submit(task, reply=lambda answer, thinking="", steps=None: tell_user(answer, chat), chat=chat, via="schedule")

    from . import proactive, watchers
    watchers.set_notifier(notify)
    watchers.load_reminders()

    def _reach_hint() -> str:
        """Warn if a future alert can't reach the phone when the app is closed (no push set up)."""
        from . import push
        if push.have_subs():
            return ""
        return (" One thing — phone notifications aren't set up yet, so I can only alert you while the Jarvis "
                "app is open. Add Jarvis to your Home Screen and turn on notifications in Settings to hear from me "
                "when it's closed.")

    def main_tool(name: str, args: dict) -> str:   # tools that must live in this process
        if name == "notify_when":
            return watchers.add(args["kind"], args["label"], args.get("name", ""), args.get("path", "")) + _reach_hint()
        if name == "cancel_watches":
            return watchers.clear()
        if name == "call_user":
            return ring_phone(args.get("message", ""))
        if name == "camera_photo":
            ph = _bridge["phone"]
            if not (ph and ph.clients):
                return "Open the Jarvis app on your phone first, then ask again and I'll send a camera photo."
            ph.send_camera("cam-" + str(int(time.time())))
            return "I've sent a photo from your camera to your phone."
        if name == "screens_photo":
            ph = _bridge["phone"]
            if not (ph and ph.clients):
                return "Open the Jarvis app on your phone first, then ask again and I'll send your screens."
            threading.Thread(target=ph.send_screens, args=("scr-" + str(int(time.time())),), daemon=True).start()
            return "I've sent a picture of your screens to your phone."
        if name == "remind_me":
            msg = watchers.remind(args.get("message", ""), args.get("at", ""), args.get("in_minutes") or 0,
                                  bool(args.get("call")))
            return msg + (_reach_hint() if not args.get("call") else "")
        if name == "list_watches":
            return watchers.listing() + "\n" + proactive.listing()
        if name == "message_user":
            text = (args.get("text") or "").strip()
            ph = _bridge["phone"]
            if not text:
                return "There was nothing to send."
            if current["via"] == "pc" or not ph:
                speak.say(text)
                return "Said it out loud. Carry on, and don't repeat it in your final answer."
            ph.message(text, current["chat"], reply_to=current["msg_id"])
            return "Sent. Carry on, and don't repeat it in your final answer."
        if name == "schedule_task":
            try:
                msg = proactive.schedule(args.get("task", ""), args.get("at", ""), args.get("in_minutes") or 0,
                                         args.get("repeat", ""), current["chat"])
            except ValueError as e:
                return str(e)
            return msg + _reach_hint()
        if name == "cancel_scheduled":
            return proactive.cancel(args.get("id", "")) or "There's no scheduled task with that id."
        return "Unknown action."

    os.environ.update(approval.serve(
        ask_user, lambda payload: _bridge["phone"] and _bridge["phone"].push(payload), main_tool))
    brain = make_brain()
    speak.start()
    jobs: queue.Queue = queue.Queue()
    working = {"text": None}   # the command being worked on right now, if any

    def submit(text: str, reply=None, chat=None, recent=None, progress=None, via: str = "pc",
               msg_id: str | None = None) -> None:
        busy_with = working["text"]
        if busy_with and stopping.is_stop_phrase(text):   # "Jarvis, stop" mid-task: don't queue it, stop
            stop_command()
            if reply:
                reply("Okay, stopping.", "", [])
            return
        tid = activity.start(text, via=via, chat=chat, msg_id=msg_id, status="queued")
        jobs.put((tid, text, reply, chat, recent, progress, via, msg_id))
        if busy_with and progress:   # say so, instead of looking like nothing is happening
            try:
                progress(f"Waiting for “{busy_with[:40]}” to finish…")
            except Exception:
                log.debug("progress callback failed", exc_info=True)

    def run_job(tid, text, reply, chat, recent, progress, via, msg_id=None) -> None:
        current.update(via=via, chat=chat, msg_id=msg_id or "")
        working["text"] = text
        stopping.begin()
        started = time.time()
        activity.set_status(tid, "running")
        log.info("Command: %s", text)

        def step(msg: str, kind: str = "") -> None:   # every step is saved and shown live
            activity.step(tid, kind or "Step", msg)
            if progress:
                try:
                    progress(msg, kind)
                except Exception:
                    log.debug("progress callback failed", exc_info=True)

        mode = modes.match(text)  # "work mode" etc. runs instantly, no Claude needed
        thinking, ok = "", True
        try:
            if mode:
                step(f"Starting {mode} mode…", "Mode")
                answer = modes.run(mode)
            else:
                answer = nlu.try_handle(text, progress=step)  # common commands, no Claude, no tokens
                if answer is not None:
                    log.info("(handled locally, no tokens used)")
                elif settings["use_claude"]:                   # not understood locally -> Claude
                    answer = brain.handle(text, chat=chat, recent=recent, progress=step)
                    thinking = getattr(brain, "last_thinking", "") or ""
                else:                                          # Claude is switched off: stay free
                    answer = ("I didn't catch that one, and Claude is switched off to save tokens. "
                              "Try a simpler command, or turn Claude back on in Settings.")
        except stopping.Stopped:
            log.info("Stopped by the user.")
            step("Stopped", "Command")
            answer = "Okay, I stopped."
        except Exception as e:
            log.exception("Command failed")
            answer, ok = f"Something went wrong: {e}", False
        if not activity.steps_of(tid):   # an instant command: still leave a record of what was done
            cmd = nlu.clean(text) or text
            step(cmd[:1].upper() + cmd[1:], "Command")
        activity.finish(tid, answer, ok=ok)
        # every exchange goes into the shared memory, so the next command (whichever brain gets it) knows about it
        memory.record(chat, text, answer, [s["t"] for s in activity.steps_of(tid)], started)
        log.info("Jarvis: %s", answer)
        if reply:
            reply(answer, thinking, activity.steps_of(tid))
        else:
            speak.say(answer)

    def worker() -> None:
        while True:
            job = jobs.get()
            try:
                run_job(*job)
            except Exception:
                # Nothing may kill this thread: if it died, every later command would sit in the queue forever
                # (the "first command works, the next ones do nothing" bug).
                log.exception("Delivering a command's result failed; carrying on with the next one.")
            finally:
                working["text"] = None

    threading.Thread(target=worker, daemon=True, name="worker").start()

    def stop_command(target: str = "") -> None:
        """The stop button. `target` is the phone message it was pressed on: if that one hasn't started yet it
        just leaves the queue; if it's already done, nothing happens (a different command may be running now)."""
        if target and target != current["msg_id"]:
            with jobs.mutex:
                job = next((j for j in jobs.queue if j[7] == target), None)
                if job:
                    jobs.queue.remove(job)
            if job:
                tid, reply = job[0], job[2]
                log.info("Stop: dropped the queued command %r.", job[1])
                activity.finish(tid, "Okay, I won't do that one.", ok=True)
                if reply:
                    reply("Okay, I won't do that one.", "", activity.steps_of(tid))
            return
        if working["text"]:
            log.info("Stop: stopping %r.", working["text"])
            stopping.stop()

    if settings["phone_enabled"] and not args.no_phone:
        from .remote import PhoneChannel
        phone = PhoneChannel(submit)
        phone.on_stop = stop_command
        _bridge["phone"] = phone   # let tools push files to the phone via the approval bridge
        threading.Thread(target=phone.run, daemon=True, name="phone").start()
    proactive.set_handlers(run_scheduled, tell_user,
                           lambda: phone and phone.soon("activity", 0.3, phone.send_activity))
    proactive.start()   # scheduled tasks, new-email heads-ups, the good-morning message

    listener = None
    if settings["voice_enabled"] and not args.no_voice:
        from .overlay import OverlayClient
        from .voice import VoiceListener
        overlay = OverlayClient(on_event=lambda e: listener.cancel() if e == "cancel" and listener else None)
        if settings["overlay_enabled"]:
            overlay.start()

        def voice_command(text: str, source: str = "wake") -> None:
            overlay.partial(text)   # show the finished command in the pop-up
            overlay.thinking()
            # Speak the reply unless the user turned off speaking for the Ctrl+Alt+J shortcut.
            speak_this = settings["speak_replies"] and (source != "hotkey" or settings["speak_on_shortcut"])

            def reply(answer: str, thinking: str = "", steps=None) -> None:
                overlay.reply(answer)
                if speak_this:
                    speak.say(answer)

            # show each step in the pop-up while it works, so it never just sits there
            submit(text, reply=reply, via="pc", progress=lambda msg, kind="": overlay.step(msg))

        listener = VoiceListener(voice_command, on_wake=overlay.show,
                                 on_partial=overlay.partial, on_idle=overlay.hide)
        threading.Thread(target=listener.run, daemon=True, name="voice").start()

        from . import hotkey   # a keyboard shortcut (Ctrl+Alt+J) that starts/stops listening without the wake word
        hotkey.configure(settings["hotkey"], listener.trigger)
        if settings["hotkey_enabled"]:
            hotkey.set_enabled(True)

    # A tray icon lets Jarvis run with no console window (double-click Jarvis.vbs, or start it at login);
    # right-click it to pick a mic or quit. --text keeps the console for typing, so skip the tray there.
    if settings["tray_enabled"] and not args.no_tray and not args.text:
        try:
            from .tray import Tray
            # Quitting from the tray ends the process at once, so stop the Claude Code session first (it would
            # otherwise keep running in the background).
            tray = Tray(listener=listener, on_quit=lambda: getattr(brain, "close", lambda: None)(),
                        on_stop=stop_command, busy=lambda: bool(working["text"]))
            threading.Thread(target=tray.run, daemon=True, name="tray").start()
        except Exception:
            log.debug("couldn't start the tray icon", exc_info=True)

    speak.say("Jarvis online.")
    try:
        if args.text:
            print("Type a command (Ctrl+C to quit).")
            while True:
                line = input("> ").strip()
                if line:
                    done = threading.Event()

                    def show(answer: str, thinking: str = "", steps=None, done=done) -> None:
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

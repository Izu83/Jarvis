"""Email: read the inbox over IMAP, sort important from not, and send replies over SMTP (only with the user's OK).

Works with any IMAP mailbox; Gmail gets extra signal from its own "Important" marker and inbox tabs. Sign-in uses an
app password kept in .env (EMAIL_APP_PASSWORD), never the real account password. Checking never marks mail as read.
"""
import email
import email.utils
import html
import imaplib
import re
import smtplib
from email.header import decode_header, make_header
from email.message import EmailMessage

from .config import secret, settings

TIMEOUT = 20
# Senders and words that almost always mean newsletters, receipts and robots rather than a person.
_BULK_SENDER = re.compile(r"no-?reply|do-?not-?reply|newsletter|notifications?@|mailer|marketing|news@|info@|"
                          r"updates?@|promo|deals|offers", re.I)
_URGENT = re.compile(r"\b(urgent|asap|important|action required|deadline|today|tomorrow|interview|invoice due|"
                     r"overdue|payment failed|security alert|verify|password|meeting|exam|contract)\b", re.I)


class NotSetUp(Exception):
    pass


def _account() -> tuple[str, str]:
    address, password = settings.get("email_address", ""), secret("EMAIL_APP_PASSWORD")
    if not address or not password:
        raise NotSetUp("Email isn't set up yet: put your address in config.json (email_address) and an app "
                       "password in .env (EMAIL_APP_PASSWORD).")
    return address, password.replace(" ", "")


def _hosts(address: str) -> tuple[str, str]:
    domain = address.rsplit("@", 1)[-1].lower()
    guess = {"gmail.com": ("imap.gmail.com", "smtp.gmail.com"), "googlemail.com": ("imap.gmail.com", "smtp.gmail.com"),
             "outlook.com": ("outlook.office365.com", "smtp.office365.com"),
             "hotmail.com": ("outlook.office365.com", "smtp.office365.com"),
             "live.com": ("outlook.office365.com", "smtp.office365.com"),
             "yahoo.com": ("imap.mail.yahoo.com", "smtp.mail.yahoo.com"),
             "icloud.com": ("imap.mail.me.com", "smtp.mail.me.com")}.get(domain, (f"imap.{domain}", f"smtp.{domain}"))
    return settings.get("email_imap_host") or guess[0], settings.get("email_smtp_host") or guess[1]


def _connect() -> imaplib.IMAP4_SSL:
    address, password = _account()
    imap = imaplib.IMAP4_SSL(_hosts(address)[0], timeout=TIMEOUT)
    imap.login(address, password)
    imap.select("INBOX", readonly=True)   # read-only: looking never marks anything as read
    return imap


def _dec(value) -> str:
    try:
        return str(make_header(decode_header(value or ""))).strip()
    except Exception:
        return str(value or "").strip()


def _search(imap, *criteria) -> list[bytes]:
    try:
        typ, data = imap.uid("SEARCH", None, *criteria)
        return data[0].split() if typ == "OK" and data and data[0] else []
    except imaplib.IMAP4.error:
        return []


def _gmail_search(imap, query: str) -> set[bytes]:
    if "gmail" not in _hosts(settings.get("email_address", ""))[0]:
        return set()
    return set(_search(imap, "X-GM-RAW", '"' + query + '"'))


def _body_text(msg: email.message.Message) -> str:
    """The readable text of a message: the plain-text part, or the HTML part with tags stripped."""
    plain, rich = None, None
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_maintype() == "multipart" or part.get("Content-Disposition", "").startswith("attachment"):
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        try:
            text = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        except Exception:
            continue
        if ctype == "text/plain" and plain is None:
            plain = text
        elif ctype == "text/html" and rich is None:
            rich = text
    if plain is None and rich is not None:
        rich = re.sub(r"(?is)<(script|style).*?</\1>", " ", rich)
        plain = html.unescape(re.sub(r"<[^>]+>", " ", rich))
    return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n\s*\n+", "\n\n", plain or "")).strip()


def _sort(sender: str, subject: str, headers, uid: bytes, sets: dict) -> tuple[bool, str]:
    """Important or not, plus a short reason."""
    name, addr = email.utils.parseaddr(sender)
    vips = [v.lower() for v in settings.get("email_vip", [])]
    if any(v and (v in addr.lower() or v in name.lower()) for v in vips):
        return True, "a sender you marked as important"
    if uid in sets["promo"]:
        return False, "promotions / social / updates tab"
    bulk = bool(headers.get("List-Unsubscribe") or headers.get("List-Id")
                or str(headers.get("Precedence", "")).lower() in ("bulk", "list", "junk"))
    if bulk or _BULK_SENDER.search(addr):
        return False, "newsletter or automated sender"
    if uid in sets["important"]:
        return True, "Gmail marked it important"
    if _URGENT.search(subject):
        return True, "looks time-sensitive"
    return True, "from a person"


def check(unread_only: bool = True, limit: int = 15) -> list[dict]:
    """Newest inbox emails (unread by default), each sorted into important or not."""
    imap = _connect()
    try:
        uids = _search(imap, "UNSEEN" if unread_only else "ALL")[-max(1, min(int(limit), 50)):]
        if not uids:
            return []
        flag = "is:unread " if unread_only else ""
        sets = {"promo": _gmail_search(imap, flag + "(category:promotions OR category:social OR category:updates "
                                              "OR category:forums)"),
                "important": _gmail_search(imap, flag + "is:important")}
        out = []
        for uid in reversed(uids):   # newest first
            typ, data = imap.uid("FETCH", uid, "(BODY.PEEK[HEADER] BODY.PEEK[TEXT]<0.4000>)")
            if typ != "OK":
                continue
            raw = b"".join(p[1] for p in data if isinstance(p, tuple))
            msg = email.message_from_bytes(raw)
            sender, subject = _dec(msg.get("From")), _dec(msg.get("Subject")) or "(no subject)"
            important, why = _sort(sender, subject, msg, uid, sets)
            snippet = _body_text(msg)
            out.append({"id": uid.decode(), "from": sender, "subject": subject, "date": msg.get("Date", ""),
                        "snippet": re.sub(r"\s+", " ", snippet)[:160], "important": important, "why": why})
        return out
    finally:
        imap.logout()


def summary(unread_only: bool = True, limit: int = 15) -> str:
    """check() as text for the brain (and the phone)."""
    mails = check(unread_only, limit)
    if not mails:
        return "No unread emails in the inbox." if unread_only else "The inbox is empty."
    imp = [m for m in mails if m["important"]]
    rest = [m for m in mails if not m["important"]]

    def line(m):
        name = email.utils.parseaddr(m["from"])[0] or email.utils.parseaddr(m["from"])[1]
        return f"- [id {m['id']}] {name}: {m['subject']} — {m['snippet']} ({m['why']})"
    parts = [f"{len(mails)} {'unread ' if unread_only else ''}email(s)."]
    parts.append(f"IMPORTANT ({len(imp)}):\n" + "\n".join(map(line, imp)) if imp else "IMPORTANT: none.")
    parts.append(f"NOT IMPORTANT ({len(rest)}):\n" + "\n".join(map(line, rest)) if rest else "NOT IMPORTANT: none.")
    return "\n\n".join(parts)


def spoken_summary(unread_only: bool = True) -> str:
    """A short sentence or two for voice, built without any model."""
    mails = check(unread_only, 15)
    if not mails:
        return "You have no unread emails."
    imp = [m for m in mails if m["important"]]
    rest = len(mails) - len(imp)

    def who(m):
        name, addr = email.utils.parseaddr(m["from"])
        return f"{name or addr} about {m['subject']}"
    if not imp:
        return f"You have {rest} unread email{'s' * (rest != 1)}, and none of them look important."
    head = f"You have {len(imp)} important email{'s' * (len(imp) != 1)}: " + "; ".join(map(who, imp[:4]))
    if len(imp) > 4:
        head += f"; and {len(imp) - 4} more"
    return head + (f". Plus {rest} that aren't important." if rest else ".")


def _fetch(uid: str) -> email.message.Message:
    imap = _connect()
    try:
        typ, data = imap.uid("FETCH", str(uid).encode(), "(BODY.PEEK[])")
        raw = b"".join(p[1] for p in data if isinstance(p, tuple)) if typ == "OK" else b""
        if not raw:
            raise ValueError(f"No email with id {uid} in the inbox.")
        return email.message_from_bytes(raw)
    finally:
        imap.logout()


def read(uid: str) -> str:
    msg = _fetch(uid)
    body = _body_text(msg)
    if len(body) > 6000:
        body = body[:6000] + "\n…(cut off)"
    return (f"From: {_dec(msg.get('From'))}\nTo: {_dec(msg.get('To'))}\nDate: {msg.get('Date', '')}\n"
            f"Subject: {_dec(msg.get('Subject'))}\n\n{body}")


def build_reply(uid: str, text: str, reply_all: bool = False) -> EmailMessage:
    """The reply, threaded under the original (same subject + In-Reply-To/References)."""
    address, _ = _account()
    orig = _fetch(uid)
    subject = _dec(orig.get("Subject"))
    reply = EmailMessage()
    reply["From"] = address
    reply["To"] = orig.get("Reply-To") or orig.get("From")
    if reply_all:
        others = [a for _, a in email.utils.getaddresses(orig.get_all("To", []) + orig.get_all("Cc", []))
                  if a and a.lower() != address.lower()]
        if others:
            reply["Cc"] = ", ".join(others)
    reply["Subject"] = subject if subject.lower().startswith("re:") else "Re: " + subject
    if orig.get("Message-ID"):
        reply["In-Reply-To"] = orig["Message-ID"]
        reply["References"] = (orig.get("References", "") + " " + orig["Message-ID"]).strip()
    quoted = "\n".join("> " + l for l in _body_text(orig)[:3000].splitlines())
    when, who = orig.get("Date", ""), _dec(orig.get("From"))
    reply.set_content(f"{text.strip()}\n\nOn {when}, {who} wrote:\n{quoted}\n")
    return reply


def send(msg: EmailMessage) -> None:
    address, password = _account()
    host = _hosts(address)[1]
    if "office365" in host:   # Outlook only offers STARTTLS on 587
        with smtplib.SMTP(host, 587, timeout=TIMEOUT) as smtp:
            smtp.starttls()
            smtp.login(address, password)
            smtp.send_message(msg)
        return
    with smtplib.SMTP_SSL(host, 465, timeout=TIMEOUT) as smtp:
        smtp.login(address, password)
        smtp.send_message(msg)

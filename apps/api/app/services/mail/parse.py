"""Reading an incoming email: headers, addresses, text, sanitized HTML and attachments.

Everything in an email is untrusted. HTML is sanitized here, on the server: no scripts, styles, forms,
frames, event handlers or images (remote images are tracking pixels and can leak that a message was read);
links keep only http(s) and mailto and open with rel="noopener noreferrer nofollow". The web app shows the
result in a sandboxed iframe on top of that.
"""

import hashlib
import hmac
import html
import re
import uuid
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr

import nh3

from app.core.config import get_settings

MAX_TEXT = 100_000
MAX_HTML = 300_000
# Headers kept on the stored message: what review, threading and loop checks need. Nothing else is stored.
KEEP_HEADERS = (
    "date",
    "return-path",
    "reply-to",
    "auto-submitted",
    "precedence",
    "x-autoreply",
    "x-autorespond",
    "x-auto-response-suppress",
    "list-id",
    "list-unsubscribe",
    "x-spam-flag",
    "x-spam-status",
    "authentication-results",
    "x-verity-loop",
    "x-mailer",
    "feedback-id",
    "x-campaign",
    "x-mailchimp-campaign",
    "x-sg-eid",
    "x-ses-outgoing",
    "x-mailgun-tag",
    "content-type",
)

SAFE_TAGS = set(nh3.ALLOWED_TAGS) - {"img", "map", "area"}
SAFE_ATTRS = {
    "a": {"href", "title"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
    "*": {"dir", "lang"},
}


@dataclass
class Attachment:
    filename: str
    content_type: str
    data: bytes


@dataclass
class ParsedMail:
    message_id: str
    in_reply_to: str | None
    references: list[str]
    from_addr: str
    from_name: str
    to: list[str]
    cc: list[str]
    subject: str
    text: str
    html: str | None
    headers: dict
    attachments: list[Attachment] = field(default_factory=list)
    content_type: str = "text/plain"
    msg: EmailMessage | None = None


_MID = re.compile(r"<[^<>\s]{1,900}>")


def normalize_mid(value: str | None) -> str | None:
    """`<abc@host>` as stored and compared: angle brackets, no whitespace, at most 998 characters."""
    if not value:
        return None
    m = _MID.search(str(value))
    if m:
        return m.group(0)[:998]
    v = "".join(str(value).split())
    return f"<{v.strip('<>')}>"[:998] if v else None


def mid_list(value: str | None) -> list[str]:
    return [m[:998] for m in _MID.findall(str(value or ""))][:50]


def _header(msg: EmailMessage, name: str) -> str:
    try:
        v = msg.get(name)
    except (ValueError, TypeError, IndexError):  # malformed headers must never stop ingestion
        return ""
    return " ".join(str(v).split())[:2000] if v is not None else ""


def sanitize_html(raw: str) -> str:
    cleaned = nh3.clean(
        raw[: MAX_HTML * 3],
        tags=SAFE_TAGS,
        attributes=SAFE_ATTRS,
        clean_content_tags={"script", "style", "noscript", "template", "iframe", "object", "embed", "svg", "math", "head", "title"},
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer nofollow",
        strip_comments=True,
    )
    return cleaned[:MAX_HTML]


def html_to_text(raw: str) -> str:
    s = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", raw)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>|</h\d>", "\n", s)
    s = nh3.clean(s, tags=set())
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n\s*\n\s*\n+", "\n\n", s)
    return s.strip()


def _part_text(part) -> str:
    try:
        return part.get_content()
    except (LookupError, ValueError, AssertionError, UnicodeError):
        payload = part.get_payload(decode=True) or b""
        return payload.decode("utf-8", "replace")


def parse(raw: bytes) -> ParsedMail:
    msg: EmailMessage = BytesParser(policy=policy.default).parsebytes(raw)
    text_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[Attachment] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        disp = (part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        if disp == "attachment" or (filename and ctype not in ("text/plain", "text/html")):
            data = part.get_payload(decode=True) or b""
            attachments.append(Attachment(filename or "attachment", ctype, data))
        elif ctype == "text/plain":
            text_parts.append(_part_text(part))
        elif ctype == "text/html":
            html_parts.append(_part_text(part))
        elif ctype in ("message/delivery-status", "message/rfc822"):
            continue
    raw_html = "\n".join(html_parts) if html_parts else None
    text = "\n\n".join(t.strip() for t in text_parts if t.strip()) or (html_to_text(raw_html) if raw_html else "")
    from_name, from_addr = parseaddr(_header(msg, "From"))
    # The website's contact form: mail sent by Verity itself, with the visitor in Reply-To. For that one sender (and
    # only when the form is configured) the visitor is the requester; for anyone else Reply-To is ignored.
    trusted = get_settings().contact_sender
    if trusted and (from_addr or "").strip().lower() == trusted:
        reply_name, reply_addr = parseaddr(_header(msg, "Reply-To"))
        if reply_addr and "@" in reply_addr:
            from_name, from_addr = reply_name, reply_addr
    message_id = normalize_mid(_header(msg, "Message-ID"))
    if not message_id:
        # No Message-ID: derive a stable one from the bytes, so a re-delivery is still recognised.
        message_id = f"<sha256.{hashlib.sha256(raw).hexdigest()}@verity.invalid>"
    headers = {h: _header(msg, h) for h in KEEP_HEADERS if _header(msg, h)}
    return ParsedMail(
        message_id=message_id,
        in_reply_to=normalize_mid(_header(msg, "In-Reply-To")),
        references=mid_list(_header(msg, "References")),
        from_addr=(from_addr or "").strip().lower()[:320],
        from_name=" ".join((from_name or "").split())[:200],
        to=[a.lower()[:320] for _, a in getaddresses([_header(msg, "To")]) if a][:50],
        cc=[a.lower()[:320] for _, a in getaddresses([_header(msg, "Cc")]) if a][:50],
        subject=_header(msg, "Subject")[:998],
        text=text[:MAX_TEXT],
        html=sanitize_html(raw_html) if raw_html else None,
        headers=headers,
        attachments=attachments,
        content_type=msg.get_content_type(),
        msg=msg,
    )


# ---- Quoted history -------------------------------------------------------------------------------

REPLY_MARKER = "##- Reply above this line -##"
_QUOTE_INTRO = re.compile(
    r"^(On .{4,300}wrote:|Am .{4,300}schrieb .{1,200}:|Le .{4,300}a écrit\s?:|El .{4,300}escribió:|-----\s*Original Message\s*-----|"
    r"From: .{3,300}|Von: .{3,300}|De : .{3,300})\s*$",
    re.IGNORECASE,
)


def strip_quoted(text: str) -> str:
    """The new part of a reply: above our marker, above "On … wrote:", without trailing `>` quotes."""
    if REPLY_MARKER in text:
        text = text.split(REPLY_MARKER, 1)[0]
    lines = text.splitlines()
    out: list[str] = []
    for line in lines:
        if _QUOTE_INTRO.match(line.strip()) and out:
            break
        out.append(line)
    while out and (out[-1].startswith(">") or not out[-1].strip()):
        out.pop()
    cleaned = "\n".join(out).strip()
    return cleaned or text.strip()


# ---- Our own Message-IDs ---------------------------------------------------------------------------


def _sig(token: str) -> str:
    return hmac.new(get_settings().secret_key.encode(), f"mid:{token}".encode(), hashlib.sha256).hexdigest()[:16]


def new_message_id(address: str | None) -> str:
    """A Message-ID Verity can recognise as its own without a lookup: <v1.{token}.{signature}@{domain}>."""
    domain = (address or "").rsplit("@", 1)[-1].strip().lower() if address and "@" in address else "verity.local"
    domain = re.sub(r"[^a-z0-9.\-]", "", domain) or "verity.local"
    token = uuid.uuid4().hex
    return f"<v1.{token}.{_sig(token)}@{domain}>"


_OWN = re.compile(r"<v1\.([0-9a-f]{32})\.([0-9a-f]{16})@[^>]+>")


def is_own_message_id(mid: str | None) -> bool:
    m = _OWN.fullmatch(mid or "")
    return bool(m) and hmac.compare_digest(m.group(2), _sig(m.group(1)))

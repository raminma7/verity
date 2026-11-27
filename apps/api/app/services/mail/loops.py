"""Bounces, automatic replies and mail loops. Checked before anything else, by headers only.

- bounce: a delivery report or a message from a mailer daemon. Stored, never a ticket, never answered.
- auto_reply: an out-of-office or other automatic message (Auto-Submitted, Precedence: auto_reply,
  X-Autoreply). Stored, never answered; it doesn't reopen a ticket.
- loop: our own message coming back (signed Message-ID, X-Verity-Loop, or sent from one of the workspace's own
  mailboxes). Stored and dropped.

`suppress_reply` is set for mail that may become a ticket but must never get an automatic reply: bulk and
list mail, senders that ask for no automatic responses, and no-reply addresses.
"""

import re
from dataclasses import dataclass, field

from app.services.mail.parse import ParsedMail, is_own_message_id

DAEMONS = {"mailer-daemon", "postmaster", "mail-daemon", "mailerdaemon", "bounce", "bounces"}
NO_REPLY = re.compile(r"^(no[-_.]?reply|do[-_.]?not[-_.]?reply|noreply|notifications?|alerts?)([+\-.].*)?$")
LOOP_HEADER = "X-Verity-Loop"


@dataclass
class LoopCheck:
    status: str | None = None  # bounce | auto_reply | loop
    suppress_reply: bool = False
    reasons: list[str] = field(default_factory=list)


def check(p: ParsedMail, own_addresses: set[str]) -> LoopCheck:
    h = {k: v.lower() for k, v in p.headers.items()}
    r = LoopCheck()
    local = p.from_addr.split("@", 1)[0] if "@" in p.from_addr else p.from_addr

    if is_own_message_id(p.message_id):
        r.reasons.append("own_message_id")
    if h.get("x-verity-loop"):
        r.reasons.append("loop_header")
    if p.from_addr and p.from_addr in own_addresses:
        r.reasons.append("own_address")
    if r.reasons:
        r.status, r.suppress_reply = "loop", True
        return r

    if p.content_type == "multipart/report" or "report-type=delivery-status" in h.get("content-type", ""):
        r.reasons.append("delivery_report")
    if local in DAEMONS:
        r.reasons.append("mailer_daemon")
    if h.get("return-path", "").strip() == "<>" or not p.from_addr:
        r.reasons.append("null_sender")
    if r.reasons:
        r.status, r.suppress_reply = "bounce", True
        return r

    auto = h.get("auto-submitted", "")
    if auto and auto.split(";")[0].strip() != "no":
        r.reasons.append("auto_submitted")
    if h.get("precedence", "").strip() == "auto_reply":
        r.reasons.append("precedence_auto_reply")
    if h.get("x-autoreply") or h.get("x-autorespond"):
        r.reasons.append("x_autoreply")
    if r.reasons:
        r.status, r.suppress_reply = "auto_reply", True
        return r

    if h.get("precedence", "").strip() in ("bulk", "junk", "list"):
        r.suppress_reply = True
        r.reasons.append("precedence_bulk")
    if h.get("list-id") or h.get("list-unsubscribe"):
        r.suppress_reply = True
        r.reasons.append("mailing_list")
    if any(w in h.get("x-auto-response-suppress", "") for w in ("all", "oof", "autoreply")):
        r.suppress_reply = True
        r.reasons.append("suppress_requested")
    if NO_REPLY.match(local):
        r.suppress_reply = True
        r.reasons.append("no_reply_sender")
    return r

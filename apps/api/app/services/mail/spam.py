"""Spam and promotion rules: cheap, deterministic, and recorded on the message so a reviewer sees why.

Strong hits decide on their own. Weak hits are passed to the AI classification call as hints and decide
nothing without it: when AI is unavailable, mail without a strong hit becomes a ticket, so nothing a
customer wrote is lost to a guess.
"""

from dataclasses import dataclass, field

from app.services.mail.parse import ParsedMail

ESP_HEADERS = ("feedback-id", "x-campaign", "x-mailchimp-campaign", "x-sg-eid", "x-mailgun-tag")
PROMO_WORDS = ("unsubscribe", "% off", "limited time", "special offer", "newsletter", "view in browser", "sale ends")
SPAM_WORDS = ("lottery", "you have won", "winner", "crypto giveaway", "wire transfer", "viagra", "claim your prize", "inheritance")


@dataclass
class RuleVerdict:
    verdict: str | None = None  # spam | promotion | legitimate; None: unsure
    strong: bool = False
    hits: list[str] = field(default_factory=list)


def _listed(address: str, entries: list[str]) -> bool:
    address = address.lower()
    domain = address.rsplit("@", 1)[-1]
    for e in entries or []:
        e = e.strip().lower()
        if not e:
            continue
        if e == address or e.lstrip("@") == domain or (e.startswith("*.") and domain.endswith(e[1:])):
            return True
    return False


def check(p: ParsedMail, settings: dict) -> RuleVerdict:
    r = RuleVerdict()
    if _listed(p.from_addr, settings.get("block_senders", [])):
        return RuleVerdict("spam", True, ["block_list"])
    if _listed(p.from_addr, settings.get("allow_senders", [])):
        return RuleVerdict("legitimate", True, ["allow_list"])
    if not settings.get("spam_filter", True):
        return RuleVerdict("legitimate", True, ["filter_off"])

    h = {k: v.lower() for k, v in p.headers.items()}
    if h.get("x-spam-flag", "").strip() == "yes" or h.get("x-spam-status", "").startswith("yes"):
        r.hits.append("spam_flag_header")
    auth = h.get("authentication-results", "")
    if "spf=fail" in auth and ("dmarc=fail" in auth or "dkim=fail" in auth):
        r.hits.append("authentication_failed")
    if r.hits:
        r.verdict, r.strong = "spam", True
        return r

    promo = []
    if h.get("list-unsubscribe"):
        promo.append("list_unsubscribe")
    if h.get("list-id"):
        promo.append("list_id")
    if h.get("precedence", "").strip() in ("bulk", "list", "junk"):
        promo.append("precedence_bulk")
    promo += [f"esp:{k}" for k in ESP_HEADERS if h.get(k)]
    body = f"{p.subject}\n{p.text[:5000]}".lower()
    words = [w for w in PROMO_WORDS if w in body]
    spammy = [w for w in SPAM_WORDS if w in body]
    r.hits = promo + [f"word:{w}" for w in words + spammy]
    if "list_unsubscribe" in promo and len(promo) >= 2:
        r.verdict, r.strong = "promotion", True
    elif promo or words:
        r.verdict = "promotion"
    elif spammy:
        r.verdict = "spam"
    return r

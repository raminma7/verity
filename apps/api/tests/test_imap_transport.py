"""The IMAP/SMTP transport against stand-ins for imaplib and smtplib: no network."""

import imaplib
import smtplib
from email.message import EmailMessage

import pytest

from app.services.mail.transport import AccountConfig, ImapSmtpTransport, TransportError

CFG = AccountConfig("s@acmehelp.com", "s@acmehelp.com", "pw", "imap.x", 993, "ssl", "smtp.x", 465, "ssl")


class StubIMAP:
    def __init__(self, messages: dict[int, bytes], validity=7, uidnext=None, fail_login=False):
        self.messages, self.validity, self.fail_login = messages, validity, fail_login
        self.uidnext = uidnext or (max(messages) + 1 if messages else 1)
        self.readonly = None
        self.logged_out = False

    def login(self, user, pw):
        if self.fail_login:
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")
        return "OK", [b"logged in"]

    def select(self, box, readonly=False):
        self.readonly = readonly
        return "OK", [str(len(self.messages)).encode()]

    def response(self, name):
        return name, [str(self.validity if name == "UIDVALIDITY" else self.uidnext).encode()]

    def status(self, box, what):
        return "OK", [b"INBOX (UIDNEXT 1 UIDVALIDITY 1)"]

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            lo = int(args[1].split()[1].split(":")[0])
            found = [u for u in sorted(self.messages) if u >= lo] or [max(self.messages, default=0)]
            return "OK", [" ".join(str(u) for u in found).encode()]
        uid, what = int(args[0]), args[1]
        data = self.messages[uid]
        if "RFC822.SIZE" in what:
            return "OK", [f"{uid} (UID {uid} RFC822.SIZE {len(data)})".encode()]
        if "HEADER" in what:
            return "OK", [(f"{uid} (BODY[HEADER] {{10}}".encode(), data.split(b"\r\n\r\n")[0]), b")"]
        return "OK", [(f"{uid} (BODY[] {{{len(data)}}}".encode(), data), b")"]

    def logout(self):
        self.logged_out = True


class StubSMTP:
    def __init__(self, fail_login=False, refuse=None):
        self.fail_login, self.refuse, self.sent = fail_login, refuse, []

    def login(self, user, pw):
        if self.fail_login:
            raise smtplib.SMTPAuthenticationError(535, b"bad")

    def send_message(self, msg, from_addr=None):
        if self.refuse:
            raise self.refuse
        self.sent.append((from_addr, msg))

    def quit(self):
        pass


def raw(i: int) -> bytes:
    return f"Message-ID: <m{i}@x>\r\nSubject: {i}\r\n\r\nbody {i}".encode()


async def test_verify_starts_at_the_end_of_the_mailbox():
    imap = StubIMAP({1: raw(1), 2: raw(2)})
    t = ImapSmtpTransport(lambda a: imap, lambda a: StubSMTP())
    assert await t.verify(CFG) == {"uidvalidity": 7, "last_uid": 2}
    assert imap.readonly is True and imap.logged_out


async def test_fetch_reads_new_uids_in_batches_and_never_changes_the_mailbox():
    imap = StubIMAP({u: raw(u) for u in (3, 4, 5, 9)})
    t = ImapSmtpTransport(lambda a: imap, lambda a: StubSMTP())
    r = await t.fetch(CFG, {"uidvalidity": 7, "last_uid": 3}, 2)
    assert [m.uid for m in r.messages] == [4, 5] and r.more and r.state == {"uidvalidity": 7, "last_uid": 5}
    assert r.messages[0].data == raw(4) and imap.readonly is True
    r = await t.fetch(CFG, r.state, 2)
    assert [m.uid for m in r.messages] == [9] and not r.more
    r = await t.fetch(CFG, r.state, 2)
    assert r.messages == [] and r.state["last_uid"] == 9  # SEARCH returns the last UID when nothing is new


async def test_a_renumbered_mailbox_is_rescanned():
    imap = StubIMAP({1: raw(1), 2: raw(2)}, validity=8)
    t = ImapSmtpTransport(lambda a: imap, lambda a: StubSMTP())
    r = await t.fetch(CFG, {"uidvalidity": 7, "last_uid": 50}, 10)
    assert [m.uid for m in r.messages] == [1, 2] and r.state["uidvalidity"] == 8


async def test_oversized_messages_come_back_as_headers(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "mail_max_message_mb", 0)
    imap = StubIMAP({2: raw(2)})
    t = ImapSmtpTransport(lambda a: imap, lambda a: StubSMTP())
    r = await t.fetch(CFG, {"uidvalidity": 7, "last_uid": 1}, 10)
    assert r.messages[0].too_large and b"body" not in r.messages[0].data


@pytest.mark.parametrize(
    ("imap", "smtp", "code"),
    [
        (StubIMAP({}, fail_login=True), StubSMTP(), "email_auth_failed"),
        (StubIMAP({}), StubSMTP(fail_login=True), "email_auth_failed"),
    ],
)
async def test_sign_in_errors_have_stable_codes(imap, smtp, code):
    t = ImapSmtpTransport(lambda a: imap, lambda a: smtp)
    with pytest.raises(TransportError) as e:
        await t.verify(CFG)
    assert e.value.code == code and not e.value.retryable


async def test_connection_errors_are_retryable():
    def boom(a):
        raise ConnectionRefusedError()

    t = ImapSmtpTransport(boom, boom)
    with pytest.raises(TransportError) as e:
        await t.verify(CFG)
    assert e.value.code == "email_connect_failed" and e.value.retryable


async def test_plaintext_connections_are_refused():
    t = ImapSmtpTransport()
    for cfg in (
        AccountConfig("s@acmehelp.com", "u", "p", "imap.x", 143, "none", "smtp.x", 25, "ssl"),
        AccountConfig("s@acmehelp.com", "u", "p", "imap.x", 993, "ssl", "smtp.x", 25, "none"),
    ):
        with pytest.raises(TransportError) as e:
            await (t.verify(cfg) if cfg.imap_security == "none" else t.send(cfg, EmailMessage()))
        assert e.value.code == "email_tls_required"


async def test_send_maps_refusals():
    msg = EmailMessage()
    msg["To"] = "x@example.org"
    ok = StubSMTP()
    await ImapSmtpTransport(lambda a: None, lambda a: ok).send(CFG, msg)
    assert ok.sent[0][0] == CFG.address
    refused = StubSMTP(refuse=smtplib.SMTPRecipientsRefused({"x@example.org": (550, b"no")}))
    with pytest.raises(TransportError) as e:
        await ImapSmtpTransport(lambda a: None, lambda a: refused).send(CFG, msg)
    assert e.value.code == "email_recipient_refused" and not e.value.retryable
    busy = StubSMTP(refuse=smtplib.SMTPDataError(451, b"try later"))
    with pytest.raises(TransportError) as e:
        await ImapSmtpTransport(lambda a: None, lambda a: busy).send(CFG, msg)
    assert e.value.retryable

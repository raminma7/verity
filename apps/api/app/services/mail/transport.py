"""Mail transport: how Verity talks to a mailbox. Like the payment provider, it sits behind an interface.

`ImapSmtpTransport` (production) fetches over IMAP and sends over SMTP, always with TLS (implicit TLS or
STARTTLS; plaintext is refused). Before connecting, the host is resolved and refused if it points at a
private, loopback, link-local (cloud metadata) or reserved address, and the connection is pinned to the
address that was checked, so a mailbox setting can't be used to reach internal services.

`FakeTransport` (tests, CI, e2e) keeps mailboxes in Redis so the API and the worker share them. Mail is put
in with `deliver()` (the dev endpoint `POST /api/dev/mailbox/{address}/deliver`) and what was sent is read
with `sent()`.

The first fetch after connecting starts at the mailbox's current end: connecting a mailbox never turns its
history into tickets.
"""

import asyncio
import contextlib
import imaplib
import re
import smtplib
import socket
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol

from app.core.config import get_settings
from app.core.ratelimit import get_redis
from app.core.ssrf import ip_blocked


class TransportError(Exception):
    """A stable code the account shows (email_auth_failed, email_connect_failed, email_tls_required, …)."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass
class AccountConfig:
    """What a transport needs to reach one mailbox. The password is decrypted only for the call."""

    address: str
    username: str
    password: str
    imap_host: str
    imap_port: int
    imap_security: str
    smtp_host: str
    smtp_port: int
    smtp_security: str


@dataclass
class RawMail:
    uid: int
    data: bytes  # the whole message, or only its header when `too_large`
    size: int = 0
    too_large: bool = False


@dataclass
class FetchResult:
    messages: list[RawMail] = field(default_factory=list)
    state: dict = field(default_factory=dict)  # {uidvalidity, last_uid}
    more: bool = False  # more new mail is waiting beyond this batch


class MailTransport(Protocol):
    async def verify(self, acct: AccountConfig) -> dict:
        """Signs in to both servers. Returns the fetch state to start from (the mailbox's current end)."""
        ...

    async def fetch(self, acct: AccountConfig, state: dict, limit: int) -> FetchResult: ...

    async def send(self, acct: AccountConfig, msg: EmailMessage) -> None: ...


# ---- IMAP / SMTP ---------------------------------------------------------------------------------


def _check_security(security: str) -> None:
    if security not in ("ssl", "starttls"):
        raise TransportError("email_tls_required", "Use SSL/TLS or STARTTLS. Verity never sends passwords unencrypted.")


def resolve_checked(host: str, port: int) -> str:
    """The address to connect to, after refusing private and internal networks."""
    host = (host or "").strip().lower()
    if not host or len(host) > 255 or not re.fullmatch(r"[a-z0-9.\-:\[\]]+", host):
        raise TransportError("email_connect_failed", "Enter a valid server name.")
    try:
        infos = socket.getaddrinfo(host.strip("[]"), port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        raise TransportError("email_connect_failed", f"Couldn't find the server {host}.", retryable=True) from None
    ips = sorted({i[4][0] for i in infos})
    if not ips:
        raise TransportError("email_connect_failed", f"Couldn't find the server {host}.", retryable=True)
    if not get_settings().crawl_allow_private and any(ip_blocked(ip) for ip in ips):
        raise TransportError("email_host_blocked", "This server is on a private network, which Verity can't connect to.")
    return ips[0]


def _tls() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


class _PinnedIMAP4(imaplib.IMAP4):
    def __init__(self, host: str, ip: str, port: int, timeout: float):
        self._ip = ip
        super().__init__(host, port, timeout=timeout)

    def _create_socket(self, timeout):
        return socket.create_connection((self._ip, self.port), timeout)


class _PinnedIMAP4_SSL(imaplib.IMAP4_SSL):
    def __init__(self, host: str, ip: str, port: int, timeout: float):
        self._ip = ip
        super().__init__(host, port, ssl_context=_tls(), timeout=timeout)

    def _create_socket(self, timeout):
        sock = socket.create_connection((self._ip, self.port), timeout)
        return self.ssl_context.wrap_socket(sock, server_hostname=self.host)


class _PinnedSMTP(smtplib.SMTP):
    def __init__(self, host: str, ip: str, port: int, timeout: float):
        self._ip = ip
        super().__init__(timeout=timeout)
        self._host = host
        self.connect(host, port)

    def _get_socket(self, host, port, timeout):
        return socket.create_connection((self._ip, port), timeout)


class _PinnedSMTP_SSL(smtplib.SMTP_SSL):
    def __init__(self, host: str, ip: str, port: int, timeout: float):
        self._ip = ip
        super().__init__(timeout=timeout, context=_tls())
        self._host = host
        self.connect(host, port)

    def _get_socket(self, host, port, timeout):
        sock = socket.create_connection((self._ip, port), timeout)
        return self.context.wrap_socket(sock, server_hostname=self._host)


def _imap_ok(typ: str, data, what: str):
    if typ != "OK":
        raise TransportError("email_fetch_failed", f"The mail server refused {what}.", retryable=True)
    return data


class ImapSmtpTransport:
    """Blocking imaplib/smtplib calls, run in a thread with timeouts. Only the INBOX is read, never changed."""

    def __init__(self, imap_factory=None, smtp_factory=None):
        # Tests inject stand-ins for imaplib/smtplib; production connects with the pinned classes above.
        self.imap_factory = imap_factory or self._connect_imap
        self.smtp_factory = smtp_factory or self._connect_smtp

    @staticmethod
    def _connect_imap(acct: AccountConfig):
        _check_security(acct.imap_security)
        ip = resolve_checked(acct.imap_host, acct.imap_port)
        timeout = get_settings().mail_timeout_seconds
        if acct.imap_security == "ssl":
            return _PinnedIMAP4_SSL(acct.imap_host, ip, acct.imap_port, timeout)
        conn = _PinnedIMAP4(acct.imap_host, ip, acct.imap_port, timeout)
        conn.starttls(ssl_context=_tls())
        return conn

    @staticmethod
    def _connect_smtp(acct: AccountConfig):
        _check_security(acct.smtp_security)
        ip = resolve_checked(acct.smtp_host, acct.smtp_port)
        timeout = get_settings().mail_timeout_seconds
        if acct.smtp_security == "ssl":
            return _PinnedSMTP_SSL(acct.smtp_host, ip, acct.smtp_port, timeout)
        conn = _PinnedSMTP(acct.smtp_host, ip, acct.smtp_port, timeout)
        conn.starttls(context=_tls())
        return conn

    def _imap_session(self, acct: AccountConfig):
        try:
            conn = self.imap_factory(acct)
        except TransportError:
            raise
        except ssl.SSLError as e:
            raise TransportError("email_tls_failed", f"Couldn't start a secure connection to {acct.imap_host}.") from e
        except (OSError, imaplib.IMAP4.error) as e:
            raise TransportError("email_connect_failed", f"Couldn't connect to {acct.imap_host}:{acct.imap_port}.", retryable=True) from e
        try:
            conn.login(acct.username, acct.password)
        except imaplib.IMAP4.error as e:
            _logout(conn)
            raise TransportError("email_auth_failed", "The mail server didn't accept this username and app password.") from e
        except OSError as e:
            _logout(conn)
            raise TransportError("email_connect_failed", f"The connection to {acct.imap_host} dropped.", retryable=True) from e
        return conn

    def _select(self, conn) -> tuple[int, int]:
        """Selects the INBOX read-only; returns (UIDVALIDITY, UIDNEXT)."""
        _imap_ok(*conn.select("INBOX", readonly=True), "to open the inbox")
        validity = _status_number(conn, "UIDVALIDITY")
        uidnext = _status_number(conn, "UIDNEXT")
        return validity, uidnext

    def _verify(self, acct: AccountConfig) -> dict:
        conn = self._imap_session(acct)
        try:
            validity, uidnext = self._select(conn)
        except OSError as e:
            raise TransportError("email_connect_failed", "The connection dropped while opening the inbox.", retryable=True) from e
        finally:
            _logout(conn)
        self._smtp_check(acct)
        return {"uidvalidity": validity, "last_uid": max(0, uidnext - 1)}

    def _smtp_check(self, acct: AccountConfig) -> None:
        conn = self._smtp_session(acct)
        with contextlib.suppress(OSError, smtplib.SMTPException):
            conn.quit()

    def _smtp_session(self, acct: AccountConfig):
        try:
            conn = self.smtp_factory(acct)
        except TransportError:
            raise
        except ssl.SSLError as e:
            raise TransportError("email_tls_failed", f"Couldn't start a secure connection to {acct.smtp_host}.") from e
        except (OSError, smtplib.SMTPException) as e:
            raise TransportError("email_connect_failed", f"Couldn't connect to {acct.smtp_host}:{acct.smtp_port}.", retryable=True) from e
        try:
            conn.login(acct.username, acct.password)
        except smtplib.SMTPAuthenticationError as e:
            raise TransportError("email_auth_failed", "The outgoing mail server didn't accept this username and app password.") from e
        except (OSError, smtplib.SMTPException) as e:
            raise TransportError("email_connect_failed", f"Couldn't sign in to {acct.smtp_host}.", retryable=True) from e
        return conn

    def _fetch(self, acct: AccountConfig, state: dict, limit: int) -> FetchResult:
        cap = get_settings().mail_max_message_mb * 1024 * 1024
        conn = self._imap_session(acct)
        try:
            validity, uidnext = self._select(conn)
            last = int(state.get("last_uid") or 0)
            if state.get("uidvalidity") not in (None, validity):
                # The server renumbered the mailbox. Rescan it; the Message-ID index drops what we already have.
                last = 0
            if not state:
                return FetchResult([], {"uidvalidity": validity, "last_uid": max(0, uidnext - 1)})
            data = _imap_ok(*conn.uid("SEARCH", None, f"UID {last + 1}:*"), "to list new mail")
            uids = sorted(int(u) for u in (data[0] or b"").split() if int(u) > last)
            batch, more = uids[:limit], len(uids) > limit
            out: list[RawMail] = []
            for uid in batch:
                size = _fetch_size(conn, uid)
                if size > cap:
                    header = _fetch_part(conn, uid, "BODY.PEEK[HEADER]")
                    out.append(RawMail(uid, header, size, too_large=True))
                else:
                    raw = _fetch_part(conn, uid, "BODY.PEEK[]")
                    out.append(RawMail(uid, raw, len(raw)))
            new_last = batch[-1] if batch else last
            return FetchResult(out, {"uidvalidity": validity, "last_uid": new_last}, more)
        except (OSError, imaplib.IMAP4.abort) as e:
            raise TransportError("email_connect_failed", "The connection to the mail server dropped.", retryable=True) from e
        except imaplib.IMAP4.error as e:
            raise TransportError("email_fetch_failed", "The mail server refused a request.", retryable=True) from e
        finally:
            _logout(conn)

    def _send(self, acct: AccountConfig, msg: EmailMessage) -> None:
        conn = self._smtp_session(acct)
        try:
            conn.send_message(msg, from_addr=acct.address)
        except smtplib.SMTPRecipientsRefused as e:
            raise TransportError("email_recipient_refused", "The mail server refused the recipient address.") from e
        except smtplib.SMTPResponseException as e:
            raise TransportError(
                "email_send_failed", f"The mail server refused the message ({e.smtp_code}).", retryable=e.smtp_code < 500
            ) from e
        except (OSError, smtplib.SMTPException) as e:
            raise TransportError("email_send_failed", "The connection dropped while sending.", retryable=True) from e
        finally:
            with contextlib.suppress(OSError, smtplib.SMTPException):
                conn.quit()

    async def verify(self, acct: AccountConfig) -> dict:
        return await asyncio.to_thread(self._verify, acct)

    async def fetch(self, acct: AccountConfig, state: dict, limit: int) -> FetchResult:
        return await asyncio.to_thread(self._fetch, acct, state, limit)

    async def send(self, acct: AccountConfig, msg: EmailMessage) -> None:
        await asyncio.to_thread(self._send, acct, msg)


def _logout(conn) -> None:
    # Logging out of a broken connection must never mask the real error.
    with contextlib.suppress(Exception):
        conn.logout()


def _status_number(conn, name: str) -> int:
    typ, data = conn.response(name)
    if data and data[0] is not None:
        try:
            return int(data[0])
        except (TypeError, ValueError):
            pass
    typ, data = conn.status("INBOX", f"({name})")
    _imap_ok(typ, data, "to report the inbox status")
    m = re.search(rf"{name} (\d+)".encode(), data[0] or b"")
    return int(m.group(1)) if m else 0


def _fetch_size(conn, uid: int) -> int:
    data = _imap_ok(*conn.uid("FETCH", str(uid), "(RFC822.SIZE)"), "to read a message size")
    m = re.search(rb"RFC822\.SIZE (\d+)", b" ".join(d if isinstance(d, bytes) else b" ".join(d) for d in data if d))
    return int(m.group(1)) if m else 0


def _fetch_part(conn, uid: int, what: str) -> bytes:
    data = _imap_ok(*conn.uid("FETCH", str(uid), f"({what})"), "to read a message")
    for part in data:
        if isinstance(part, tuple) and len(part) >= 2:
            return part[1]
    return b""


# ---- Fake (tests, CI, e2e) -------------------------------------------------------------------------

FAKE_BAD_PASSWORD = "wrong-password"
FAKE_UNREACHABLE = "unreachable.invalid"


def _fake_key(address: str, box: str) -> str:
    return f"verity:fakemail:{address.strip().lower()}:{box}"


class FakeTransport:
    """Mailboxes in Redis: `inbox` holds raw messages (UID = position + 1), `sent` what Verity sent."""

    def _check(self, acct: AccountConfig) -> None:
        _check_security(acct.imap_security)
        _check_security(acct.smtp_security)
        if FAKE_UNREACHABLE in (acct.imap_host, acct.smtp_host):
            raise TransportError("email_connect_failed", f"Couldn't connect to {acct.imap_host}:{acct.imap_port}.", retryable=True)
        if acct.password == FAKE_BAD_PASSWORD:
            raise TransportError("email_auth_failed", "The mail server didn't accept this username and app password.")

    async def verify(self, acct: AccountConfig) -> dict:
        self._check(acct)
        n = await get_redis().llen(_fake_key(acct.address, "inbox"))
        return {"uidvalidity": 1, "last_uid": n}

    async def fetch(self, acct: AccountConfig, state: dict, limit: int) -> FetchResult:
        self._check(acct)
        r = get_redis()
        last = int(state.get("last_uid") or 0)
        total = await r.llen(_fake_key(acct.address, "inbox"))
        items = await r.lrange(_fake_key(acct.address, "inbox"), last, last + limit - 1)
        cap = get_settings().mail_max_message_mb * 1024 * 1024
        out = []
        for i, item in enumerate(items):
            raw = item.encode("utf-8", "surrogateescape") if isinstance(item, str) else item
            if len(raw) > cap:
                head = raw.split(b"\r\n\r\n", 1)[0] if b"\r\n\r\n" in raw else raw.split(b"\n\n", 1)[0]
                out.append(RawMail(last + i + 1, head, len(raw), too_large=True))
            else:
                out.append(RawMail(last + i + 1, raw, len(raw)))
        new_last = last + len(items)
        return FetchResult(out, {"uidvalidity": 1, "last_uid": new_last}, total > new_last)

    async def send(self, acct: AccountConfig, msg: EmailMessage) -> None:
        self._check(acct)
        await get_redis().rpush(_fake_key(acct.address, "sent"), msg.as_bytes().decode("utf-8", "surrogateescape"))

    # Test and dev helpers

    @staticmethod
    async def deliver(address: str, raw: bytes | str) -> int:
        text = raw.decode("utf-8", "surrogateescape") if isinstance(raw, bytes) else raw
        return await get_redis().rpush(_fake_key(address, "inbox"), text)

    @staticmethod
    async def sent(address: str) -> list[str]:
        return list(await get_redis().lrange(_fake_key(address, "sent"), 0, -1))

    @staticmethod
    async def reset(address: str) -> None:
        await get_redis().delete(_fake_key(address, "inbox"), _fake_key(address, "sent"))


_transport: MailTransport | None = None


def get_transport() -> MailTransport:
    global _transport
    if _transport is None:
        _transport = FakeTransport() if get_settings().mail_transport == "fake" else ImapSmtpTransport()
    return _transport


def set_transport(t: MailTransport | None) -> None:
    global _transport
    _transport = t

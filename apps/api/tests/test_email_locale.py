"""System emails go out in the recipient's interface language."""

import re
import string
from datetime import date

from app.services import email as mail
from app.services.email_text import LOCALES, TEXT, day, number, render
from tests.conftest import last_token, new_client, signup, subscribe


def _fields(s: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(s) if f}


def test_every_email_exists_in_every_language_with_the_same_placeholders():
    for key, langs in TEXT.items():
        assert set(langs) == set(LOCALES), key
        en_subject, en_body = langs["en"]
        for locale, (subject, body) in langs.items():
            assert _fields(subject) == _fields(en_subject), (key, locale)
            assert _fields(body) == _fields(en_body), (key, locale)


def test_render_fills_every_placeholder_and_falls_back_to_english():
    values = {f: "X" for langs in TEXT.values() for s in langs["en"] for f in _fields(s)}
    for key in TEXT:
        for locale in LOCALES:
            subject, body = render(key, locale, **values)
            assert "{" not in subject + body, (key, locale)
    assert render("verify", "tr", **values) == render("verify", "en", **values)


def test_dates_and_numbers_follow_the_language():
    d = date(2026, 3, 5)
    assert day(d, "en") == "5 Mar 2026"
    assert day(d, "de") == "5. März 2026"
    assert day(d, "fr") == "5 mars 2026"
    assert day(d, "ar") == "5 مارس 2026"
    assert number(1234567, "en") == "1,234,567"
    assert number(1234567, "de") == "1.234.567"


async def _set_locale(c, locale: str) -> None:
    assert (await c.patch("/api/me", json={"locale": locale})).status_code == 200


async def test_password_reset_email_uses_the_account_language(client):
    await signup(client, "hans@example.com", "Hans Muster")
    await _set_locale(client, "de")
    mail.OUTBOX.clear()
    assert (await client.post("/api/auth/forgot-password", json={"email": "hans@example.com"})).status_code == 202
    m = mail.OUTBOX[-1]
    assert m.subject == "Setze dein Verity-Passwort zurück"
    assert m.text.startswith("Hallo Hans Muster,")
    assert m.headers["Content-Language"] == "de"
    assert last_token("hans@example.com", "/reset-password")


async def test_arabic_email_html_is_right_to_left():
    html = mail._html("مرحبًا\nhttps://x.test/a", ["https://x.test/a"], "ar")
    assert 'dir="rtl"' in html and 'lang="ar"' in html
    assert 'dir="ltr"' in mail._html("Hi", [], "en")


async def test_billing_receipt_reaches_each_contact_in_their_language(client):
    await signup(client, "claire@example.com", "Claire")
    await _set_locale(client, "fr")
    mail.OUTBOX.clear()
    await subscribe(client)
    receipt = next(m for m in mail.OUTBOX if m.to == "claire@example.com" and m.subject.startswith("Reçu"))
    assert "Paiement reçu pour l’espace de travail" in receipt.text
    # The renewal date is spelled in French, e.g. "28 déc. 2026".
    assert re.search(r"Renouvellement le : \d{1,2} \S+ \d{4}", receipt.text)
    assert receipt.headers["Content-Language"] == "fr"


async def test_invitation_uses_the_invitees_language_when_they_have_an_account(client):
    async with new_client() as other:
        await signup(other, "lucia@example.com", "Lucía")
        await _set_locale(other, "es")
    await signup(client, "owner@example.com", "Owner")
    await subscribe(client)
    roles = {r["key"]: r["id"] for r in (await client.get("/api/workspace/roles")).json()}
    agent = next(v for k, v in roles.items() if k != "owner")
    mail.OUTBOX.clear()
    r = await client.post("/api/workspace/invitations", json={"email": "lucia@example.com", "role_id": agent})
    assert r.status_code == 201, r.text
    invite = mail.OUTBOX[-1]
    assert invite.subject.startswith("Owner te ha invitado a ")
    assert invite.headers["Content-Language"] == "es"
    # A new address gets the inviter's language (English here).
    r = await client.post("/api/workspace/invitations", json={"email": "new@example.com", "role_id": agent})
    assert r.status_code == 201, r.text
    assert mail.OUTBOX[-1].headers["Content-Language"] == "en"


async def test_the_profile_accepts_exactly_the_six_interface_languages(client):
    await signup(client, "poly@example.com", "Poly Glot")
    for locale in ("en", "es", "de", "fr", "it", "ar"):
        r = await client.patch("/api/me", json={"locale": locale})
        assert r.status_code == 200 and r.json()["locale"] == locale, (locale, r.text)
    for locale in ("fa", "tr", "pt"):
        r = await client.patch("/api/me", json={"locale": locale})
        assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_locale"

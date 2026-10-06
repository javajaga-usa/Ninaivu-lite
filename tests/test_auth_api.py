"""Signing in and changing a password, over HTTP (A32): the username login and
its console-only check, the profile picker's PIN, and /api/me/password, each
with the wrong secret and with the limits that follow it."""

from __future__ import annotations

import pytest

from ninaivu_lite import api_auth, auth, db

from conftest import sign_in

ADMIN_PASSWORD = "admin passphrase"         # conftest's "appa"


@pytest.fixture(autouse=True)
def fresh_limits(monkeypatch):
    """The limits live for the whole process; each test starts with none used."""
    for name, value in vars(api_auth).copy().items():
        if isinstance(value, auth.Throttle):
            monkeypatch.setattr(api_auth, name, auth.Throttle(limit=value.limit,
                                                              window=value.window))


def conn_of(app):
    return db.connect(app.config["LITE"].data_dir)


def login(client, username, password, **extra):
    return client.post("/api/auth/login", json={"username": username, "password": password,
                                                **extra})


def me(client) -> dict:
    return client.get("/api/me").get_json()


def until_refused(send, most: int = 100) -> int:
    """Send until the answer is 429; how many it took."""
    for n in range(1, most + 1):
        status = send().status_code
        if status == 429:
            return n
        assert status in (401, 403), status
    pytest.fail(f"never limited in {most} tries")


# --- the username login ---------------------------------------------------------------


def test_login_signs_in_with_the_right_password(app):
    c = app.test_client()
    r = login(c, "APPA", ADMIN_PASSWORD)         # usernames are not case-sensitive
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True and body["user"]["username"] == "appa"
    cookie = r.headers.get("Set-Cookie", "")
    assert cookie.startswith(f"{auth.SESSION_COOKIE}=") and "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert me(c)["username"] == "appa" and me(c)["anonymous"] is False


def test_login_refuses_a_wrong_password_and_an_unknown_name_alike(app):
    c = app.test_client()
    wrong = login(c, "appa", "not the passphrase")
    nobody = login(c, "nobody-here", "not the passphrase")
    assert wrong.status_code == nobody.status_code == 401
    # The same words for both: the answer does not tell which names exist.
    assert wrong.get_json()["error"] == nobody.get_json()["error"]
    assert auth.SESSION_COOKIE not in wrong.headers.get("Set-Cookie", "")
    assert me(c)["anonymous"] is True


def test_login_is_limited_after_repeated_failures(app):
    c = app.test_client()
    tries = until_refused(lambda: login(c, "appa", "not the passphrase"))
    assert tries > 1                              # a mistyped password is not a lockout
    # While limited, not even the right password is tried from here.
    r = login(c, "appa", ADMIN_PASSWORD)
    assert r.status_code == 429
    assert me(c)["anonymous"] is True


def test_a_good_login_forgets_earlier_mistakes(app):
    c = app.test_client()
    for _ in range(api_auth.throttle_short.limit - 1):
        assert login(c, "appa", "not the passphrase").status_code == 401
    assert login(c, "appa", ADMIN_PASSWORD).status_code == 200
    # The count started again: as many mistakes as before are still not a 429.
    for _ in range(api_auth.throttle_short.limit - 1):
        assert login(c, "appa", "not the passphrase").status_code == 401


def test_the_console_signs_in_administrators_only(app):
    conn = conn_of(app)
    auth.create_user(conn, "amma", password="family passphrase", name="Amma", role="family")
    c = app.test_client()
    r = login(c, "amma", "family passphrase", console=True)
    assert r.status_code == 403
    assert "administrators" in r.get_json()["error"]
    assert auth.SESSION_COOKIE not in r.headers.get("Set-Cookie", "")
    assert me(c)["anonymous"] is True
    # The same person in the family app is let in, and an administrator in the console.
    assert login(c, "amma", "family passphrase").status_code == 200
    assert login(app.test_client(), "appa", ADMIN_PASSWORD, console=True).status_code == 200


def test_a_wrong_password_for_the_console_is_still_a_401(app):
    r = login(app.test_client(), "appa", "not the passphrase", console=True)
    assert r.status_code == 401


# --- the profile picker's PIN ------------------------------------------------------------


@pytest.fixture()
def kutti(app):
    return auth.create_user(conn_of(app), "kutti", pin="4826", name="Kutti", role="family")


def test_a_wrong_pin_is_refused_and_the_right_one_signs_in(app, kutti):
    c = app.test_client()
    r = c.post("/api/auth/enter", json={"id": kutti.id, "secret": "1111"})
    assert r.status_code == 401 and "PIN" in r.get_json()["error"]
    assert me(c)["anonymous"] is True
    r = c.post("/api/auth/enter", json={"id": kutti.id, "secret": "4826"})
    assert r.status_code == 200 and r.get_json()["user"]["username"] == "kutti"
    assert me(c)["username"] == "kutti"


def test_pin_guessing_is_limited(app, kutti):
    c = app.test_client()
    tries = until_refused(lambda: c.post("/api/auth/enter",
                                         json={"id": kutti.id, "secret": "9999"}))
    assert tries > 1
    r = c.post("/api/auth/enter", json={"id": kutti.id, "secret": "4826"})
    assert r.status_code == 429
    assert me(c)["anonymous"] is True


def test_an_unknown_or_missing_profile_is_a_400(app):
    c = app.test_client()
    assert c.post("/api/auth/enter", json={"id": 9999, "secret": "4826"}).status_code == 400
    assert c.post("/api/auth/enter", json={"secret": "4826"}).status_code == 400


# --- changing your own password ---------------------------------------------------------


def test_a_password_change_needs_the_current_password(app):
    c = app.test_client()
    sign_in(app, c)
    elsewhere = auth.start_session(conn_of(app), auth.get_user_by_name(conn_of(app), "appa").id)
    r = c.post("/api/me/password", json={"current": "not the passphrase",
                                          "password": "a brand new passphrase"})
    assert r.status_code == 403 and "current password" in r.get_json()["error"]
    # Nothing changed: the old password still signs in, the new one does not.
    assert login(app.test_client(), "appa", "a brand new passphrase").status_code == 401
    assert login(app.test_client(), "appa", ADMIN_PASSWORD).status_code == 200

    r = c.post("/api/me/password", json={"current": ADMIN_PASSWORD,
                                          "password": "a brand new passphrase"})
    assert r.status_code == 200
    assert login(app.test_client(), "appa", "a brand new passphrase").status_code == 200
    assert login(app.test_client(), "appa", ADMIN_PASSWORD).status_code == 401
    # This session goes on; every other one has ended.
    assert me(c)["username"] == "appa"
    other = app.test_client()
    other.set_cookie(auth.SESSION_COOKIE, elsewhere)
    assert me(other)["anonymous"] is True


def test_guessing_the_current_password_is_limited(app):
    c = app.test_client()
    sign_in(app, c)
    tries = until_refused(lambda: c.post("/api/me/password", json={
        "current": "not the passphrase", "password": "a brand new passphrase"}))
    assert tries > 1
    r = c.post("/api/me/password", json={"current": ADMIN_PASSWORD,
                                          "password": "a brand new passphrase"})
    assert r.status_code == 429


def test_a_too_short_new_password_is_refused(app):
    c = app.test_client()
    sign_in(app, c)
    r = c.post("/api/me/password", json={"current": ADMIN_PASSWORD, "password": "short"})
    assert r.status_code == 400
    assert login(app.test_client(), "appa", ADMIN_PASSWORD).status_code == 200


def test_signing_in_is_needed_to_change_a_password(app):
    r = app.test_client().post("/api/me/password", json={"current": ADMIN_PASSWORD,
                                                         "password": "a brand new passphrase"})
    assert r.status_code == 401


def test_a_password_given_by_the_administrator_must_be_changed_without_the_old_one(app):
    conn = conn_of(app)
    appa = auth.get_user_by_name(conn, "appa")
    auth.create_user(conn, "meena", password="given passphrase", name="Meena", role="family",
                     created_by=appa.id)
    c = app.test_client()
    r = login(c, "meena", "given passphrase")
    assert r.status_code == 200 and r.get_json()["user"]["must_change"] is True
    # The person chooses their own, without being asked for the one they were given.
    r = c.post("/api/me/password", json={"password": "meena's own passphrase"})
    assert r.status_code == 200
    assert me(c)["must_change"] is False
    assert login(app.test_client(), "meena", "meena's own passphrase").status_code == 200
    assert login(app.test_client(), "meena", "given passphrase").status_code == 401
    # Once chosen, a change asks for it again.
    r = c.post("/api/me/password", json={"password": "yet another passphrase"})
    assert r.status_code == 403

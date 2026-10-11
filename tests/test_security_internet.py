"""External attack surface review of 2026-10-07: A116-A119."""

from __future__ import annotations

import logging

import pytest

from ninaivu_lite import app as app_module
from ninaivu_lite import api_share, auth, db
from ninaivu_lite.config import Config
from ninaivu_lite.version import __version__

from conftest import ids, sign_in


def from_address(address, **headers):
    environ = {"REMOTE_ADDR": address}
    environ.update({f"HTTP_{k.upper().replace('-', '_')}": v for k, v in headers.items()})
    return {"environ_base": environ}


@pytest.fixture(autouse=True)
def nobody_on_this_network(monkeypatch):
    """No public address is on the test machine's own network, whatever it is."""
    monkeypatch.setattr(app_module, "_same_network", lambda ip: False)
    monkeypatch.delenv("NINAIVU_ALLOW_INTERNET", raising=False)
    app_module._internet_refused.clear()


# --- A116: a request from the internet is refused unless allowed on purpose ----------------


@pytest.mark.parametrize("address", ["127.0.0.1", "::1", "192.168.1.30", "10.0.0.5",
                                     "172.20.1.1", "169.254.3.4", "fe80::1", "fd00::5",
                                     "100.101.102.103", "::ffff:192.168.1.30"])
def test_a116_home_addresses_are_answered(app, address):
    r = app.test_client().get("/api/auth/state", **from_address(address))
    assert r.status_code == 200


@pytest.mark.parametrize("address", ["8.8.8.8", "1.1.1.1", "2606:4700::1111",
                                     "::ffff:8.8.8.8"])
def test_a116_internet_addresses_are_refused(app, address, caplog):
    client = app.test_client()
    with caplog.at_level(logging.WARNING):
        r = client.get("/api/auth/state", **from_address(address))
    assert r.status_code == 403
    assert r.json["error"] == "Ninaivu Lite answers only the home network."
    assert any("allow_internet" in rec.getMessage() for rec in caplog.records)
    # Pages say so in both languages, and sign-in is refused too.
    page = client.get("/", **from_address(address))
    assert page.status_code == 403 and "வீட்டு" in page.get_data(as_text=True)
    assert client.post("/api/auth/login", json={"username": "appa", "password": "x"},
                       **from_address(address)).status_code == 403
    assert client.get("/share/anything", **from_address(address)).status_code == 403


def test_a116_the_same_network_counts_as_home(app, monkeypatch):
    # A campus or ISP network that hands out public addresses, or a phone's
    # global IPv6 address at home: on this computer's own network.
    monkeypatch.setattr(app_module, "_same_network", lambda ip: str(ip) == "8.8.8.9")
    assert app.test_client().get("/api/auth/state", **from_address("8.8.8.9")).status_code == 200
    assert app.test_client().get("/api/auth/state", **from_address("8.8.4.4")).status_code == 403


@pytest.mark.parametrize("header,value", [
    ("X-Forwarded-For", "8.8.8.8"),
    ("X-Forwarded-For", "192.168.1.5, 8.8.8.8"),
    ("X-Real-IP", "1.1.1.1"),
    ("CF-Connecting-IP", "2606:4700::1111"),
    ("Forwarded", 'for="[2606:4700::1111]:4711";proto=https'),
    ("Forwarded", "for=8.8.8.8:51000"),
])
def test_a116_a_tunnel_on_this_computer_does_not_open_it_to_the_internet(app, header, value):
    r = app.test_client().get("/api/auth/state", **from_address("127.0.0.1", **{header: value}))
    assert r.status_code == 403


@pytest.mark.parametrize("value", ["192.168.1.5", "100.64.1.2", "unknown", "_hidden", ""])
def test_a116_a_home_proxy_still_works(app, value):
    r = app.test_client().get("/api/auth/state",
                              **from_address("192.168.1.2", **{"X-Forwarded-For": value}))
    assert r.status_code == 200


def test_a116_the_household_can_allow_the_internet(app, monkeypatch):
    client = app.test_client()
    app.config["LITE"].allow_internet = True
    assert client.get("/api/auth/state", **from_address("8.8.8.8")).status_code == 200
    app.config["LITE"].allow_internet = False
    assert client.get("/api/auth/state", **from_address("8.8.8.8")).status_code == 403
    monkeypatch.setenv("NINAIVU_ALLOW_INTERNET", "1")
    assert client.get("/api/auth/state", **from_address("8.8.8.8")).status_code == 200


def test_a116_allow_internet_is_saved_and_read(tmp_path):
    cfg = Config(data_dir=str(tmp_path), folders=[str(tmp_path)])
    assert cfg.allow_internet is False
    cfg.allow_internet = True
    cfg.save()
    assert Config.load(str(tmp_path)).allow_internet is True


def test_a116_home_address_reads_what_proxies_write():
    home = app_module.home_address
    assert home("[fd00::5]:4711") is True
    assert home("192.168.1.5:51000") is True
    assert home("unknown") is None and home("") is None and home("not an address") is None
    assert home("224.0.0.1") is False


# --- A117: a share link's password has a daily limit ------------------------------------------


def test_a117_share_password_guessing_has_a_daily_limit(app, family, monkeypatch):
    token = family.post("/api/shares", json={"scope": "asset", "target_id": ids(app)["beach.jpg"],
                                             "password": "1234"}).json["token"]
    # Many addresses, spread over hours: the short limits never bite, the day's does.
    monkeypatch.setattr(api_share.throttle_short, "limit", 10_000)
    monkeypatch.setattr(api_share.throttle_long, "limit", 10_000)
    for n in range(api_share.throttle_day.limit):
        r = app.test_client().post(f"/api/share/{token}/unlock", json={"password": f"{n:04d}x"},
                                   **from_address(f"192.168.1.{n % 200 + 2}"))
        assert r.status_code == 401
    r = app.test_client().post(f"/api/share/{token}/unlock", json={"password": "1234"},
                               **from_address("192.168.2.9"))
    assert r.status_code == 429
    # The header way in counts against the same limit.
    r = app.test_client().get(f"/api/share/{token}", headers={"X-Share-Password": "1234"},
                              **from_address("192.168.2.10"))
    assert r.status_code == 401


# --- A118: signing in again ends this browser's previous session --------------------------------


def test_a118_switching_profile_ends_the_previous_session(app):
    c = db.connect(app.config["LITE"].data_dir)
    amma = auth.create_user(c, "amma", name="Amma", role="family")
    client = app.test_client()
    sign_in(app, client, "admin")
    admin_id = auth.get_user_by_name(c, "appa").id
    assert auth.session_counts(c).get(admin_id) == 1
    r = client.post("/api/auth/enter", json={"id": amma.id})
    assert r.status_code == 200
    assert auth.session_counts(c).get(admin_id) is None       # not left behind for 30 days
    assert auth.session_counts(c).get(amma.id) == 1
    assert client.get("/api/me").json["username"] == "amma"


# --- A119: the health check tells the network only that it is up ----------------------------------


def test_a119_health_tells_the_network_little(app):
    far = app.test_client().get("/api/health", **from_address("192.168.1.30")).json
    assert far == {"ok": True, "app": "Ninaivu Lite"}
    near = app.test_client().get("/api/health", **from_address("127.0.0.1")).json
    assert near["version"] == __version__ and "instance" in near and "busy" in near
    # A proxy on this computer passes on somebody else's request.
    proxied = app.test_client().get("/healthz", **from_address(
        "127.0.0.1", **{"X-Forwarded-For": "192.168.1.30"})).json
    assert "version" not in proxied

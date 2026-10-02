"""The Control Panel's once-a-day question: is there a newer Ninaivu Lite?"""

from __future__ import annotations

import json

from ninaivu_lite import updates


def test_versions_compare_as_numbers():
    assert updates.parse_version("v1.3.2") == (1, 3, 2)
    assert updates.parse_version("1.10.0-rc1") == (1, 10, 0)
    assert updates.is_newer("1.10.0", "1.9.9")
    assert not updates.is_newer("1.3.2", "1.3.2")
    assert not updates.is_newer("garbage", "1.0.0")


def test_check_asks_once_a_day_and_keeps_the_answer(tmp_path):
    asked = []
    clock = [1000.0]

    def fetch():
        asked.append(1)
        return {"version": "9.0.0", "url": "https://example.test/releases/v9.0.0"}

    info = updates.check(tmp_path, current="1.3.3", fetch=fetch, now=lambda: clock[0])
    assert info["available"] and info["version"] == "9.0.0"
    assert info["url"].endswith("v9.0.0") and asked == [1]
    # Within a day: the kept answer, no second question.
    clock[0] += 3600
    again = updates.check(tmp_path, current="1.3.3", fetch=fetch, now=lambda: clock[0])
    assert again["available"] and asked == [1]
    # A day later it asks again; forced, it asks at once.
    clock[0] += updates.MAX_AGE
    updates.check(tmp_path, current="1.3.3", fetch=fetch, now=lambda: clock[0])
    assert asked == [1, 1]
    updates.check(tmp_path, current="1.3.3", fetch=fetch, now=lambda: clock[0], force=True)
    assert asked == [1, 1, 1]
    # The same version is not an update.
    assert updates.check(tmp_path, current="9.0.0", fetch=fetch, now=lambda: clock[0])["available"] is False
    saved = json.loads((tmp_path / updates.STATE_FILE).read_text())
    assert saved["latest"]["version"] == "9.0.0"


def test_offline_keeps_the_last_answer_and_says_nothing_before_the_first(tmp_path):
    assert updates.check(tmp_path, current="1.3.3", fetch=lambda: None) is None
    updates.check(tmp_path, current="1.3.3", fetch=lambda: {"version": "2.0.0", "url": "u"},
                  now=lambda: 0.0)
    kept = updates.check(tmp_path, current="1.3.3", fetch=lambda: None, now=lambda: 1e9)
    assert kept["version"] == "2.0.0" and kept["available"]


def test_the_switch_lives_beside_the_answer_and_starts_off(tmp_path):
    assert updates.enabled(tmp_path) is False            # nothing is asked unasked
    updates.set_enabled(tmp_path, True)
    assert updates.enabled(tmp_path) is True
    updates.set_enabled(tmp_path, False)
    assert updates.enabled(tmp_path) is False
    updates.check(tmp_path, fetch=lambda: {"version": "1.0.0", "url": "u"}, now=lambda: 0.0)
    assert updates.enabled(tmp_path) is False          # a check does not flip the switch
    updates.set_enabled(tmp_path, True)
    assert updates.enabled(tmp_path) is True


def test_fetch_latest_reads_github_shape(monkeypatch):
    import io
    import urllib.request

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout):
        assert "api.github.com" in request.full_url and timeout == updates.TIMEOUT
        assert request.get_header("User-agent", "").startswith("Ninaivu Lite/")
        return Response(json.dumps({"tag_name": "v1.9.0", "html_url": "https://x/v1.9.0"}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert updates.fetch_latest() == {"version": "1.9.0", "url": "https://x/v1.9.0"}

    def broken(request, timeout):
        raise OSError("no network")

    monkeypatch.setattr(urllib.request, "urlopen", broken)
    assert updates.fetch_latest() is None

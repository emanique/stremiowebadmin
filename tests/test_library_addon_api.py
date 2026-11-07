"""The owner's view of the addon: where to install it from, and how to revoke it."""
from unittest.mock import patch

from fastapi.testclient import TestClient

from stremiosrv.app import create_app
from stremiosrv.config import Settings
from stremiosrv.library import api as lib
from stremiosrv.library import session as sessionmod

HTTPS = {"X-Forwarded-Proto": "https"}
USER = {"_id": "user-1", "email": "owner@example.com"}


def _client(tmp_path, **kw):
    # base_url https so the Secure cookie is actually stored by the test client -- see
    # test_library_auth_api.py's _client for why.
    s = Settings(library_ui=True, cache_root=str(tmp_path), **kw)
    return TestClient(create_app(settings=s), base_url="https://testserver")


def anonymous_client(tmp_path):
    return _client(tmp_path)


def signed_in_client(tmp_path):
    """A client with a live owner session.

    Neither test_library_auth_api.py nor test_library_session.py exposes this as a shared helper,
    so the authKey exchange test_library_auth_api.py's own tests use is copied inline here rather
    than adding a tests/helpers_library.py for the one file that would import it.
    """
    c = _client(tmp_path)
    with patch.object(lib.stremio_api, "get_user", lambda key, **kw: USER):
        r = c.post("/library/api/session", json={"authKey": "good"}, headers=HTTPS)
    assert r.status_code == 200
    return c


def test_the_install_url_carries_the_current_token_and_the_requests_origin(tmp_path):
    c = signed_in_client(tmp_path)
    r = c.get("/library/api/addon", headers={"Host": "box.invalid:12470",
                                             "X-Forwarded-Proto": "https"})
    assert r.status_code == 200
    token = sessionmod.ensure_addon_token(str(tmp_path))
    assert r.json()["url"] == f"https://box.invalid:12470/library/addon/{token}/manifest.json"


def test_resetting_mints_a_new_token_so_old_installs_stop_working(tmp_path):
    c = signed_in_client(tmp_path)
    before = c.get("/library/api/addon").json()["url"]
    after = c.post("/library/api/addon/reset").json()["url"]
    assert after != before


def test_both_routes_need_a_session(tmp_path):
    c = anonymous_client(tmp_path)
    assert c.get("/library/api/addon").status_code in (401, 403)
    assert c.post("/library/api/addon/reset").status_code in (401, 403)


def test_account_ensure_is_idempotent_and_preserves_other_addons(tmp_path, monkeypatch):
    c = TestClient(create_app(settings=Settings(library_ui=True, cache_root=str(tmp_path))),
                   base_url="https://testserver", client=("192.168.1.50", 50000))
    other = {"manifest": {"id": "other.addon"}, "transportUrl": "https://other/manifest.json", "flags": {}}
    current = [other]
    writes = []
    monkeypatch.setattr(lib.stremio_api, "get_user", lambda key, **kw: {"_id": "account-B"})
    monkeypatch.setattr(lib.stremio_api, "get_addons", lambda key, **kw: list(current))
    def save(key, addons, **kw):
        writes.append(addons)
        current[:] = addons
        return {"success": True}
    monkeypatch.setattr(lib.stremio_api, "set_addons", save)

    first = c.post("/library/api/addon/ensure", json={"authKey": "B"})
    assert first.status_code == 200 and first.json()["changed"] is True
    assert current[0] == other
    assert current[1]["manifest"]["id"] == "org.stremiosrv.library"
    second = c.post("/library/api/addon/ensure", json={"authKey": "B"})
    assert second.status_code == 200 and second.json()["changed"] is False
    assert len(writes) == 1


def test_account_ensure_replaces_stale_library_url_without_duplicate(tmp_path, monkeypatch):
    c = TestClient(create_app(settings=Settings(library_ui=True, cache_root=str(tmp_path))),
                   base_url="https://testserver", client=("192.168.1.50", 50000))
    old = {"manifest": {"id": "org.stremiosrv.library"},
           "transportUrl": "https://testserver/library/addon/OLD/manifest.json", "flags": {}}
    current = [old]
    monkeypatch.setattr(lib.stremio_api, "get_user", lambda key, **kw: {"_id": "account-A"})
    monkeypatch.setattr(lib.stremio_api, "get_addons", lambda key, **kw: list(current))
    monkeypatch.setattr(lib.stremio_api, "set_addons", lambda key, addons, **kw: current.__setitem__(slice(None), addons) or {"success": True})
    r = c.post("/library/api/addon/ensure", json={"authKey": "A"})
    assert r.status_code == 200 and r.json()["changed"] is True
    mine = [a for a in current if a.get("manifest", {}).get("id") == "org.stremiosrv.library"]
    assert len(mine) == 1
    assert "/OLD/" not in mine[0]["transportUrl"]


def test_account_ensure_owner_config_blocks_other_account_before_reading_addons(tmp_path, monkeypatch):
    c = TestClient(create_app(settings=Settings(library_ui=True, cache_root=str(tmp_path),
                                                library_owner="allowed-account")),
                   base_url="https://testserver", client=("192.168.1.50", 50000))
    monkeypatch.setattr(lib.stremio_api, "get_user",
                        lambda key, **kw: {"_id": "other-account", "email": "other@example.com"})
    def must_not_read(*args, **kwargs):
        raise AssertionError("blocked account addon collection must not be read")
    monkeypatch.setattr(lib.stremio_api, "get_addons", must_not_read)
    r = c.post("/library/api/addon/ensure", json={"authKey": "OTHER"})
    assert r.status_code == 200
    assert r.json()["allowed"] is False
    assert r.json()["changed"] is False


def test_account_ensure_owner_config_accepts_matching_email(tmp_path, monkeypatch):
    c = TestClient(create_app(settings=Settings(library_ui=True, cache_root=str(tmp_path),
                                                library_owner="owner@example.com")),
                   base_url="https://testserver", client=("192.168.1.50", 50000))
    monkeypatch.setattr(lib.stremio_api, "get_user",
                        lambda key, **kw: {"_id": "owner-id", "email": "owner@example.com"})
    current = []
    monkeypatch.setattr(lib.stremio_api, "get_addons", lambda key, **kw: list(current))
    monkeypatch.setattr(lib.stremio_api, "set_addons",
                        lambda key, addons, **kw: current.extend(addons) or {"success": True})
    r = c.post("/library/api/addon/ensure", json={"authKey": "OWNER"})
    assert r.status_code == 200
    assert r.json()["allowed"] is True and r.json()["changed"] is True
    assert current[0]["manifest"]["id"] == "org.stremiosrv.library"

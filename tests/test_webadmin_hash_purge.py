"""The WebAdmin Remove button must delete cache, not only detach a session."""
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webadmin"))
import app as webadmin  # noqa: E402


def test_webadmin_remove_dispatches_explicit_hash_purge(monkeypatch):
    ih = "ABCDEF1234567890ABCDEF1234567890ABCDEF12"
    dispatched = []
    monkeypatch.setattr(webadmin, "relay", lambda method, path: dispatched.append((method, path)) or {"ok": True})
    assert webadmin.remove(ih) == {"ok": True}
    assert dispatched == [("POST", f"/cache/purge/{ih.lower()}")]


def test_webadmin_remove_rejects_unverified_hash(monkeypatch):
    monkeypatch.setattr(webadmin, "relay", lambda *_: pytest.fail("unexpected relay"))
    with pytest.raises(HTTPException) as exc:
        webadmin.remove("../unsafe")
    assert exc.value.status_code == 400

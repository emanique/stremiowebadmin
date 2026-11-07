from fastapi.testclient import TestClient

from stremiosrv.app import create_app


class StubEngine:
    """Minimal engine stand-in so remove routes are unit-testable without libtorrent."""

    def __init__(self):
        self._t = {"abc123": object()}
        self.removed: list[str] = []
        self.all_removed = False

    def get(self, h):
        return self._t.get(h.lower())

    def remove(self, h):
        self.removed.append(h)
        self._t.pop(h.lower(), None)

    def remove_all(self):
        self.all_removed = True
        self._t.clear()


def test_remove_calls_engine():
    eng = StubEngine()
    c = TestClient(create_app(engine=eng))
    r = c.get("/abc123/remove")
    assert r.status_code == 200
    assert "abc123" in eng.removed


def test_remove_all_calls_engine():
    eng = StubEngine()
    c = TestClient(create_app(engine=eng))
    r = c.get("/removeAll")
    assert r.status_code == 200
    assert eng.all_removed is True

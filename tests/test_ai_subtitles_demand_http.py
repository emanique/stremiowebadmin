"""HTTP contract: explicit selection, heartbeat, notice and stop."""
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from stremiosrv.ai_subtitles.addon import router
from stremiosrv.subtitle_translator import addon as translator


class StubDemand:
    def __init__(self):
        self.active = None
    def start(self, infohash, index, position, audio_index=0):
        self.active = dict(sessionId="s"*32, infohash=infohash,
                           fileIndex=index, audioIndex=audio_index, positionSeconds=position,
                           stage="preparing", lastBlock=None)
        return self.active.copy()
    def heartbeat(self, sid, position):
        self.active["positionSeconds"] = position
        return self.active.copy()
    def status(self, sid):
        if self.active is None:
            raise KeyError("expired")
        return self.active.copy()
    def stop(self, sid):
        self.active = None
    def session_srt(self, sid):
        if self.active is None or self.active["sessionId"] != sid:
            raise KeyError("expired")
        return "1\n01:25:00,000 --> 01:25:03,000\nTranscrição em PT\n"


def test_explicit_http_session_lifecycle(tmp_path):
    service = StubDemand()
    app = FastAPI()
    app.include_router(router)
    app.state.ai_on_demand = service
    app.state.settings = SimpleNamespace(cache_root=str(tmp_path))
    client = TestClient(app)
    with patch.object(translator, "_guard"):
        response = client.post("/ai-subtitles/token/demand/start",
                               json={"infohash": "a"*40, "fileIndex": 0,
                                     "positionSeconds": 5100})
        assert response.status_code == 200
        sid = response.json()["sessionId"]
        response = client.get(f"/ai-subtitles/token/demand/{sid}/subtitles.srt")
        assert response.status_code == 200
        assert "01:25:00,000" in response.text
        assert "A preparar legendas" in response.text
        assert response.headers["cache-control"].startswith("no-store")
        response = client.post(f"/ai-subtitles/token/demand/{sid}/heartbeat",
                               json={"positionSeconds": 5110})
        assert response.json()["positionSeconds"] == 5110
        assert client.post(f"/ai-subtitles/token/demand/{sid}/stop").json()["stopped"]
        assert client.get(f"/ai-subtitles/token/demand/{sid}/status.json").status_code == 404


def test_native_srt_requires_existing_explicit_session_and_is_never_trigger(tmp_path):
    service = StubDemand()
    app = FastAPI()
    app.include_router(router)
    app.state.ai_on_demand = service
    app.state.settings = SimpleNamespace(cache_root=str(tmp_path))
    client = TestClient(app)
    with patch.object(translator, "_guard"):
        missing = client.get("/ai-subtitles/token/demand/unknown/native.srt")
        assert missing.status_code == 404
        assert service.active is None
        started = client.post("/ai-subtitles/token/demand/start", json={
            "infohash": "a" * 40, "fileIndex": 0, "positionSeconds": 5100,
        })
        sid = started.json()["sessionId"]
        response = client.get(f"/ai-subtitles/token/demand/{sid}/native.srt")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-subrip")
        assert response.headers["cache-control"].startswith("no-store")
        assert "Transcrição em PT" in response.text
        assert client.post(f"/ai-subtitles/token/demand/{sid}/stop").status_code == 200
        assert client.get(f"/ai-subtitles/token/demand/{sid}/native.srt").status_code == 404

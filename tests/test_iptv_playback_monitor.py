"""IPTV HLS activity maps signed jobs to channel names, not provider URLs."""
from __future__ import annotations

from types import SimpleNamespace

from stremiosrv.iptv import switches


def test_recent_iptv_activity_is_client_scoped_without_ip_address_leaks():
    switches.reset()
    now = 1000.0
    job_a = "iptv-" + "a" * 24 + "-" + "b" * 24
    job_b = "iptv-" + "c" * 24 + "-" + "d" * 24
    assert switches.switch("192.0.2.1", job_a, now=now) is None
    assert switches.switch("192.0.2.2", job_a, now=now) is None
    items = switches.active_jobs(now=now + 5)
    assert len(items) == 1
    assert items[0]["jobId"] == job_a and items[0]["viewers"] == 2
    assert not any("192.0.2" in str(item) for item in items)
    assert switches.switch("192.0.2.1", job_b, now=now + 6) is None
    assert switches.active_jobs(now=now + 20)[0]["jobId"] == job_b
    assert switches.active_jobs(now=now + 60) == []
    switches.reset()


def test_segment_touch_keeps_channel_visible_without_forged_playback():
    switches.reset()
    job = "iptv-" + "e" * 24 + "-" + "f" * 24
    switches.switch("192.0.2.3", job, now=100)
    assert switches.active_jobs(now=146) == []
    switches.touch("192.0.2.3", job, now=150)
    assert switches.active_jobs(now=160)[0]["idleSeconds"] == 10
    switches.touch("192.0.2.4", job, now=160)
    assert switches.active_jobs(now=160)[0]["viewers"] == 1
    switches.reset()


def test_core_monitor_correlates_workload_and_never_returns_source_url(monkeypatch):
    from stremiosrv.iptv import control, store
    channel = store.Channel(
        "a" * 24, "Canal 12", "Noticias",
        "https://provider.example.net/live?password=synthetic", 12, 3,
    )
    signed = "iptv-" + "a" * 24 + "-" + "b" * 24
    monkeypatch.setattr(control, "_admin", lambda *args: None)
    monkeypatch.setattr(control.switches if hasattr(control, "switches") else switches,
                        "active_jobs",
                        lambda: [{"jobId": signed, "viewers": 1, "idleSeconds": 2}])
    monkeypatch.setattr(store, "channels", lambda: {channel.id: channel})
    monkeypatch.setattr(store, "resolve_job", lambda _: channel)
    request = SimpleNamespace(app=SimpleNamespace(
        state=SimpleNamespace(
            converter=SimpleNamespace(workload_for_job=lambda _: "workload-12")
        )
    ))
    result = control.playback(request)
    assert result["count"] == 1
    assert result["active"][0]["workloadId"] == "workload-12"
    assert result["active"][0]["name"] == "Canal 12"
    assert "provider.example.net" not in str(result)
    assert "password" not in str(result)
    assert "192.0.2" not in str(result)

"""Observer is read-only; Stremio reconciler exclusively owns .env and Docker."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("gateway_observation_qa",ROOT/"scripts/gateway-port-sync.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

MAC = "bc:24:11:53:5f:65"
POLICY = "stremio"
IP = "192.168.1.199"
GW = "192.168.1.231"
NOW = 1794070000.0


def observation(*, port=40819, route="vpn-protwg", observed=None, issued=None,
                expiry=None, **changes):
    data = {
        "schema": 1, "port": port, "policy_id": POLICY,
        "registered_mac": MAC, "host_ip": IP, "gateway": GW,
        "route": route, "observed_at": NOW - 10 if observed is None else observed,
        "lease_issued_at": NOW - 15 if issued is None else issued,
        "lease_expires_at": NOW + 50 if expiry is None else expiry
    }
    data.update(changes)
    return data


def write(tmp_path, data):
    p = tmp_path/"observation.json"
    p.write_text(json.dumps(data))
    p.chmod(0o600)
    return p


def check(path):
    return mod.load_verified_observation(path,policy=POLICY,mac=MAC,host_ip=IP,gateway=GW,now=NOW)


def test_authenticated_observation_selects_new_port(tmp_path):
    assert check(write(tmp_path,observation(port=45001))) == (45001,"vpn-protwg")


@pytest.mark.parametrize("change", [
    {"host_ip":"192.168.1.229"}, {"policy_id":"other"},
    {"registered_mac":"00:11:22:33:44:55"}, {"gateway":"192.168.1.200"}
])
def test_identity_mismatch_rejected(tmp_path,change):
    with pytest.raises(mod.PortSyncError,match="identity"):
        check(write(tmp_path,observation(**change)))


@pytest.mark.parametrize("change", [
    {"observed":NOW-90}, {"observed":NOW+2},
    {"issued":NOW-100}, {"expiry":NOW+2},
    {"port":None}, {"port":True}, {"port":65536},
    {"route":"blocked"}, {"route":"other"}, {"route":"direct"}
])
def test_stale_or_invalid_observation_does_not_change_port(tmp_path,change):
    assert check(write(tmp_path,observation(**change))) is None


def test_direct_uses_6881_without_provider_lease(tmp_path):
    o=observation(port=6881,route="direct",lease_issued_at=None,lease_expires_at=None)
    assert check(write(tmp_path,o))==(6881,"direct")


def test_untrusted_permissions_rejected(tmp_path):
    path=write(tmp_path,observation())
    path.chmod(0o644)
    with pytest.raises(mod.PortSyncError,match="permissions"):
        check(path)


def test_symlink_rejected(tmp_path):
    target=write(tmp_path,observation())
    link=tmp_path/"symlink.json"
    link.symlink_to(target)
    with pytest.raises(mod.PortSyncError,match="regular file"):
        check(link)


def test_invalid_json_rejected(tmp_path):
    path=tmp_path/"observation.json"
    path.write_text("{invalid")
    path.chmod(0o600)
    with pytest.raises(mod.PortSyncError,match="invalid JSON"):
        check(path)


def test_observer_payload_must_remain_small(tmp_path):
    path=write(tmp_path,dict(observation(),unexpected="a"*5000))
    with pytest.raises(mod.PortSyncError,match="size"):
        check(path)


def test_main_consumes_observation_without_contacting_gateway(monkeypatch,tmp_path):
    state=[]
    conf=tmp_path/"port-sync.conf"
    conf.write_text("ENABLED=1\nSOURCE=observation\nPOLICY_ID=stremio\n"
                    "HOST_MAC=bc:24:11:53:5f:65\nGATEWAY_IP=192.168.1.231\n")
    observation_path=write(tmp_path,observation(port=45001))
    monkeypatch.setattr(mod,"CONF",conf)
    monkeypatch.setattr(mod,"OBSERVATION",observation_path)
    monkeypatch.setattr(mod,"LOCK",tmp_path/"lock")
    monkeypatch.setattr(mod,"route_identity",lambda mac:("192.168.1.1","ens18",IP))
    monkeypatch.setattr(mod,"reconcile",lambda p:state.append(p) or "recreated")
    assert not hasattr(mod,"fetch_snapshot")
    monkeypatch.setattr(mod,"status",lambda *a,**kw:None)
    monkeypatch.setattr(mod.time,"time",lambda:NOW)
    assert mod.main()==0
    assert state==[45001]


def test_observation_gateway_differs_from_default_router(monkeypatch,tmp_path):
    seen = []
    cfg=tmp_path/"port-sync.conf"
    cfg.write_text("ENABLED=1\nSOURCE=observation\nPOLICY_ID=stremio\n"
                   "HOST_MAC=bc:24:11:53:5f:65\nGATEWAY_IP=192.168.1.231\n")
    monkeypatch.setattr(mod,"CONF",cfg)
    monkeypatch.setattr(mod,"OBSERVATION",write(tmp_path,observation(port=45001)))
    monkeypatch.setattr(mod,"LOCK",tmp_path/"lock")
    monkeypatch.setattr(mod,"route_identity",lambda mac:("192.168.1.1","ens18",IP))
    monkeypatch.setattr(mod,"reconcile",lambda p:seen.append(p) or "recreated")
    monkeypatch.setattr(mod,"status",lambda *a,**kw:None)
    monkeypatch.setattr(mod.time,"time",lambda:NOW)
    assert mod.main()==0
    assert seen==[45001]


def test_direct_api_mode_refused(monkeypatch,tmp_path):
    conf=tmp_path/"port-sync.conf"
    conf.write_text("ENABLED=1\nSOURCE=direct\nPOLICY_ID=stremio\n"
                    "HOST_MAC=bc:24:11:53:5f:65\nGATEWAY_IP=192.168.1.231\n")
    monkeypatch.setattr(mod,"CONF",conf)
    monkeypatch.setattr(mod,"LOCK",tmp_path/"lock")
    monkeypatch.setattr(mod,"route_identity",lambda mac:("192.168.1.1","ens18",IP))
    with pytest.raises(mod.PortSyncError,match="observation"):
        mod.main()

"""Automatic authorised host observation and safe 6881 fallback."""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import time

ROOT=Path(__file__).resolve().parents[1]

def load(name,filename):
    spec=importlib.util.spec_from_file_location(name,ROOT/"scripts"/filename)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

AUTO=load("gateway_auto_observer","auto-gateway-observer.py")
SYNC=load("gateway_port_fallback","gateway-port-sync.py")

IP="192.168.1.250"
MAC="3c:2c:30:9b:c2:75"
GW="192.168.1.252"
POLICY="stremio"

def snapshot(now=None):
    now=time.time() if now is None else now
    return {
      "selected_host":{"policy_id":POLICY,"registered_mac":MAC,
                       "host_ip":IP,"route":"vpn-proton"},
      "runtime":{"owner":POLICY,"route":"vpn-proton","applied":True,
                 "status":"nat_rules_verified","provider_lease":True,
                 "external_ports":{"tcp":62743,"udp":62743},
                 "updated_at":now-4,"lease_expires_at":now+45},
      "rules_applied":True
    }

def test_valid_gateway_auth_port_accepted():
    now=time.time()
    item=AUTO.observation(snapshot(now),GW,IP,MAC,now)
    assert item["port"]==62743
    assert item["policy_id"]==POLICY
    assert item["lease_expires_at"]>now
    assert SYNC.validate(snapshot(now),POLICY,MAC,IP,now)==(62743,"vpn-proton")

def test_revoked_gateway_authorization_cannot_keep_old_port():
    now=time.time()
    snap=snapshot(now);snap["selected_host"]=None
    assert AUTO.observation(snap,GW,IP,MAC,now)["port"] is None
    assert AUTO.observation(None,GW,IP,MAC,now)["port"] is None

def test_ip_mac_tcp_udp_and_expired_lease_rejected():
    now=time.time()
    for change in ("ip","mac","port","expiry"):
        x=snapshot(now)
        if change=="ip":x["selected_host"]["host_ip"]="192.168.1.251"
        if change=="mac":x["selected_host"]["registered_mac"]="00:11:22:33:44:55"
        if change=="port":x["runtime"]["external_ports"]["udp"]=62744
        if change=="expiry":x["runtime"]["lease_expires_at"]=now+2
        assert AUTO.observation(x,GW,IP,MAC,now)["port"] is None

def test_stale_observation_restores_default_without_traffic_route_changes(tmp_path,monkeypatch):
    path=tmp_path/"last.json"
    monkeypatch.setattr(SYNC,"LAST_VERIFIED",path)
    calls=[]
    monkeypatch.setattr(SYNC,"reconcile",lambda port:calls.append(port) or "recreated")
    now=time.time()
    SYNC.mark_verified(path,POLICY,MAC,IP,GW,now)
    assert SYNC.fallback_port(POLICY,MAC,IP,GW,now+35)=="awaiting_verified_lease"
    assert calls==[]
    assert SYNC.fallback_port(POLICY,MAC,IP,GW,now+130)=="default_port_restored"
    assert calls==[6881]
    assert not path.exists()

def test_unauthorized_first_boot_uses_6881(tmp_path,monkeypatch):
    monkeypatch.setattr(SYNC,"LAST_VERIFIED",tmp_path/"absent")
    calls=[]
    monkeypatch.setattr(SYNC,"reconcile",lambda port:calls.append(port) or "unchanged")
    assert SYNC.fallback_port(POLICY,MAC,IP,GW,time.time())=="default_port_verified"
    assert calls==[6881]

def test_wrong_identity_cannot_prolong_grace(tmp_path,monkeypatch):
    path=tmp_path/"last.json"
    monkeypatch.setattr(SYNC,"LAST_VERIFIED",path)
    SYNC.mark_verified(path,POLICY,MAC,IP,GW,time.time())
    assert SYNC.last_verified_seconds(path,POLICY,MAC,"192.168.1.251",GW,time.time()) is None
    path.chmod(0o644)
    assert SYNC.last_verified_seconds(path,POLICY,MAC,IP,GW,time.time()) is None

def test_install_and_reconciler_connect_auto_gateway():
    setup=(ROOT/"packaging/stremio-webadmin-setup.sh").read_text()
    agent=(ROOT/"scripts/reconcile-host.py").read_text()
    assert "stremio-webadmin-reconcile.timer" in setup
    assert 'scripts/auto-gateway-observer.py' in agent
    assert 'scripts/gateway-port-sync.py' in agent
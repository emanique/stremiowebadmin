"""Gateway port matching must be host-scoped, fresh and symmetric TCP/UDP."""
import importlib.util
import time
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("gateway_port_sync",ROOT/"scripts/gateway-port-sync.py")
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

MAC="00:e0:4c:36:0d:26"
POLICY="stremio-221"
IP="192.168.1.221"


def snapshot(*, port=59317, route="vpn", owner=POLICY, host_ip=IP, mac=MAC,
             policy=POLICY, lease=True, now=None):
    when=time.time() if now is None else now
    return {
        "selected_host":{"policy_id":policy,"registered_mac":mac,
                         "host_ip":host_ip,"route":route},
        "rules_applied":True,
        "runtime":{"owner":owner,"route":route,"applied":True,
                   "provider_lease":lease,"status":"nat_rules_verified",
                   "external_ports":{"tcp":port,"udp":port},
                   "updated_at":when-10,"lease_expires_at":when+40}
    }


def test_valid_host_vpn_lease():
    t=time.time()
    assert mod.validate(snapshot(now=t), POLICY,MAC,IP,t)==(59317,"vpn")


@pytest.mark.parametrize("change",[
    {"host_ip":"192.168.1.199"},
    {"owner":"stremio"},
    {"policy":"stremio"},
    {"mac":"00:11:22:33:44:55"},
    {"lease":False},
])
def test_reject_wrong_identity_or_no_lease(change):
    t=time.time()
    assert mod.validate(snapshot(now=t,**change),POLICY,MAC,IP,t) is None


def test_reject_mismatched_tcp_udp():
    t=time.time()
    data=snapshot(now=t)
    data["runtime"]["external_ports"]["udp"]=59318
    assert mod.validate(data,POLICY,MAC,IP,t) is None


def test_reject_expired_or_stale_lease():
    t=time.time()
    assert mod.validate(snapshot(now=t-200),POLICY,MAC,IP,t) is None


def test_direct_requires_6881_and_no_provider_lease():
    t=time.time()
    assert mod.validate(snapshot(port=6881,route="direct",lease=False,now=t),
                        POLICY,MAC,IP,t)==(6881,"direct")
    assert mod.validate(snapshot(port=59317,route="direct",lease=False,now=t),
                        POLICY,MAC,IP,t) is None


def test_env_rewrite_single_setting():
    assert mod.update_env("X=2\nSTREMIOSRV_BT_LISTEN_PORT=6881\n",59317) == (
        "X=2\nSTREMIOSRV_BT_LISTEN_PORT=59317\n"
    )
    assert "STREMIOSRV_BT_LISTEN_PORT=59317" in mod.update_env("X=2\n",59317)

def test_port_sync_recreate_uses_gpu_aware_launcher():
    import inspect
    source=inspect.getsource(mod.reconcile)
    assert '["sh","start.sh","up","-d","--no-deps","--force-recreate"' in source
    assert '["sh","start.sh","config","--quiet"]' in source
    assert 'verify_gpu_contract()' in source
    assert '["docker","compose","up"' not in source


def test_missing_gpu_rejected_if_host_has_render_nodes(monkeypatch):
    from unittest.mock import patch
    from pathlib import Path

    class RenderNode:
        def exists(self): return True
        def __str__(self): return "/dev/dri/renderD129"

    with patch.object(Path, "glob", return_value=[RenderNode()]):
        monkeypatch.setattr(mod, "gpu_devices", lambda: [])
        with pytest.raises(mod.PortSyncError,match="VAAPI device was lost"):
            mod.verify_gpu_contract()

#!/usr/bin/env python3
"""Gateway-attested BT port synchronizer for standalone Stremio.

No credentials in repository or Docker. Selected host MAC, policy, fresh lease,
TCP/UDP equivalence and current kernel gateway must all match. Fail closed.
"""
from __future__ import annotations
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, ProxyHandler, HTTPSHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
STATUS = Path("/var/lib/stremio-webadmin/port-status.json")
LOCK = Path("/run/lock/stremio-webadmin-port-sync.lock")
CONF = Path("/etc/stremio-webadmin/port-sync.conf")
KEY = "STREMIOSRV_BT_LISTEN_PORT"


class PortSyncError(Exception):
    pass


def validate(snapshot: dict, policy: str, mac: str, local_ip: str, now: float):
    """Return (port,route) or None when gateway attestation is incomplete."""
    host = snapshot.get("selected_host") or {}
    runtime = snapshot.get("runtime") or {}
    if (not re.fullmatch(r"[a-z][a-z0-9-]{1,31}", policy)
        or not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", mac)
        or host.get("policy_id") != policy
        or str(host.get("registered_mac", "")).lower() != mac
        or host.get("host_ip") != local_ip
        or runtime.get("owner") != policy
        or host.get("route") != runtime.get("route")
        or not snapshot.get("rules_applied") or not runtime.get("applied")):
        return None
    route = host.get("route")
    ports = runtime.get("external_ports") or {}
    tcp, udp = ports.get("tcp"), ports.get("udp")
    if type(tcp) is not int or type(udp) is not int or tcp != udp or not (1 <= tcp <= 65535):
        return None
    if route == "direct":
        if runtime.get("provider_lease") or tcp != 6881:
            return None
        return tcp, route
    if runtime.get("status") != "nat_rules_verified" or not runtime.get("provider_lease"):
        return None
    issued, expiry = runtime.get("updated_at"), runtime.get("lease_expires_at")
    if (type(issued) not in (int,float) or type(expiry) not in (int,float)
        or not issued <= now <= issued + 75 or expiry - now < 12):
        return None
    return tcp, route


def run(cmd, **kwargs):
    return subprocess.run(cmd, text=True, capture_output=True, check=True,
                          timeout=kwargs.pop("timeout", 15), **kwargs)


def route_identity(expected_mac: str):
    routes=json.loads(run(["ip","-j","-4","route","get","1.1.1.1"]).stdout)
    if len(routes)!=1 or "gateway" not in routes[0]:
        raise PortSyncError("default gateway not found")
    route=routes[0]
    gw=str(ipaddress.IPv4Address(route["gateway"]))
    if not ipaddress.IPv4Address(gw).is_private:
        raise PortSyncError("gateway is not private LAN")
    nic=route["dev"]
    links=json.loads(run(["ip","-j","link","show","dev",nic]).stdout)
    if len(links)!=1 or links[0]["address"].lower()!=expected_mac:
        raise PortSyncError("active egress NIC MAC differs from registered host")
    addresses=json.loads(run(["ip","-j","-4","address","show","dev",nic]).stdout)
    ips=[x["local"] for x in addresses[0]["addr_info"]
         if x["family"]=="inet" and x["scope"]=="global"]
    if len(ips)!=1 or route.get("prefsrc",ips[0])!=ips[0]:
        raise PortSyncError("ambiguous local host IP")
    return gw, nic, ips[0]


def fetch_snapshot(gateway: str, port: int, path: str, ca: Path, token_file: Path):
    if path not in ("/api/v1/port-forward/desired","/gateway/api/v1/port-forward/desired"):
        raise PortSyncError("unsupported API path")
    if not (1 <= port <= 65535) or not ca.is_file() or token_file.is_symlink():
        raise PortSyncError("gateway TLS CA or status token not provisioned")
    if not token_file.is_file() or token_file.stat().st_mode & 0o077:
        raise PortSyncError("gateway status token must be owner-only")
    token=token_file.read_text().strip()
    if not re.fullmatch(r"[0-9a-f]{64,128}",token):
        raise PortSyncError("invalid gateway status token")
    url=f"https://{gateway}:{port}{path}"
    ctx=ssl.create_default_context(cafile=str(ca))
    opener=build_opener(ProxyHandler({}),HTTPSHandler(context=ctx))
    with opener.open(Request(url,headers={"Authorization":"Bearer "+token,
                                         "Accept":"application/json"}),timeout=8) as response:
        if response.status!=200:
            raise PortSyncError("non-200 gateway response")
        raw=response.read(65537)
    if len(raw)>65536:
        raise PortSyncError("gateway response too long")
    value=json.loads(raw)
    if not isinstance(value,dict):
        raise PortSyncError("invalid gateway payload")
    return value


def effective_port():
    data=json.loads(run(["docker","inspect","stremio-libtorrent-server"]).stdout)[0]
    env=dict(s.split("=",1) for s in data["Config"]["Env"] if "=" in s)
    port=int(env[KEY])
    for protocol in ("tcp","udp"):
        binding=data["HostConfig"]["PortBindings"].get(f"{port}/{protocol}",[])
        if not any(x.get("HostPort")==str(port) for x in binding):
            raise PortSyncError(f"missing Docker published port {protocol}")
    return port


def update_env(current: str, port: int):
    lines=current.splitlines()
    found=False
    out=[]
    for line in lines:
        if re.match(r"^\s*"+KEY+r"\s*=",line):
            if found:raise PortSyncError("duplicate port setting")
            out.append(f"{KEY}={port}")
            found=True
        else:
            out.append(line)
    if not found:out.append(f"{KEY}={port}")
    return "\n".join(out)+"\n"


def atomic_write(path: Path, value: str):
    if path.is_symlink(): raise PortSyncError("refusing symlink")
    fd,name=tempfile.mkstemp(prefix=".port-sync-",dir=path.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"w") as f:
            f.write(value)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def gpu_devices():
    """GPU mapping is part of the container's effective deployment contract."""
    data=json.loads(run(["docker","inspect","stremio-libtorrent-server"]).stdout)[0]
    return [item.get("PathOnHost") for item in (data["HostConfig"].get("Devices") or [])]


def verify_gpu_contract():
    """Fail closed if an available VAAPI render node was lost during recreation."""
    expected=[str(node) for node in Path("/dev/dri").glob("renderD*") if node.exists()]
    if not expected:
        return True
    actual=gpu_devices()
    if not any(node in actual for node in expected):
        raise PortSyncError("VAAPI device was lost during Stremio recreation")
    return True


def reconcile(port:int):
    if effective_port()==port:
        verify_gpu_contract()
        return "unchanged"
    if not ENV.is_file() or ENV.is_symlink():
        raise PortSyncError("missing or untrusted environment file")
    old=ENV.read_text()
    atomic_write(ENV,update_env(old,port))
    try:
        run(["sh","start.sh","config","--quiet"],cwd=ROOT)
        run(["sh","start.sh","up","-d","--no-deps","--force-recreate",
             "stremio-libtorrent-server"],cwd=ROOT,timeout=130)
        for _ in range(12):
            try:
                ready=run(["docker","inspect","-f","{{.State.Health.Status}}",
                           "stremio-libtorrent-server"],cwd=ROOT).stdout.strip()
                if ready=="healthy" and effective_port()==port:
                    verify_gpu_contract()
                    return "recreated"
            except (subprocess.CalledProcessError,PortSyncError,ValueError):
                pass
            time.sleep(3)
        raise PortSyncError("new port did not become healthy")
    except Exception:
        atomic_write(ENV,old)
        run(["sh","start.sh","up","-d","--no-deps","--force-recreate",
             "stremio-libtorrent-server"],cwd=ROOT,timeout=130)
        for _ in range(12):
            try:
                ready=run(["docker","inspect","-f","{{.State.Health.Status}}",
                           "stremio-libtorrent-server"],cwd=ROOT).stdout.strip()
                oldport=int(next(line.split("=",1)[1] for line in old.splitlines()
                                 if line.startswith(KEY+"=")))
                if ready=="healthy" and effective_port()==oldport:
                    verify_gpu_contract()
                    break
            except (subprocess.CalledProcessError,PortSyncError,ValueError,StopIteration):
                pass
            time.sleep(3)
        else:
            raise PortSyncError("rollback health/port verification failed")
        raise


def status(result, *, port=None, gateway=None, route=None, detail=None):
    STATUS.parent.mkdir(parents=True,exist_ok=True)
    STATUS.write_text(json.dumps({"result":result,"port":port,"gateway":gateway,
                                  "route":route,"detail":detail,
                                  "updated_at":time.time()}))
    os.chmod(STATUS,0o644)
    print(result,port or "",detail or "")


def main():
    values={}
    if CONF.is_file():
        for line in CONF.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k,v=line.split("=",1)
                values[k.strip()]=v.strip()
    if values.get("ENABLED")!="1":
        status("unconfigured",detail="Gateway host policy and dedicated token not provisioned")
        return 0
    policy=values["POLICY_ID"]
    mac=values["HOST_MAC"].lower()
    with LOCK.open("a+") as h:
        fcntl.flock(h,fcntl.LOCK_EX|fcntl.LOCK_NB)
        gw,_,host=route_identity(mac)
        expected_gateway=values.get("GATEWAY_IP","")
        if not expected_gateway or gw != expected_gateway:
            raise PortSyncError("gateway route not approved")
        snapshot=fetch_snapshot(gw,int(values.get("API_PORT","9443")),
                                values.get("API_PATH","/api/v1/port-forward/desired"),
                                Path(values["CA_FILE"]),Path(values["TOKEN_FILE"]))
        choice=validate(snapshot,policy,mac,host,time.time())
        if choice is None:
            status("no_verified_port_change",gateway=gw,
                   detail="no fresh authenticated port assignment for this host")
            return 0
        port,route=choice
        if route_identity(mac)!=(gw,_,host):
            raise PortSyncError("route changed after gateway request")
        outcome=reconcile(port)
        status(outcome,port=port,gateway=gw,route=route)
    return 0


if __name__=="__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        status("error",detail=str(exc))
        sys.exit(1)

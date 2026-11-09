#!/usr/bin/env python3
"""Automatic trusted-LAN NAT-PMP observer.

Only a Gateway-host-authorised source IP/MAC can read its own assignment.
First connection uses TOFU certificate pinning on a trusted L2 LAN.
A later certificate change fails closed until the pin is deliberately reset.
No administrative token is copied and the observer never writes Docker/.env.
"""
from __future__ import annotations
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
OBS=Path("/var/lib/stremio-webadmin/gateway-port-observation.json")
PIN=Path("/etc/stremio-webadmin/gateway-auto-tls-pin")
PORT=9443
PATH="/api/v1/port-forward/authorized-host"


def current_identity():
    route=json.loads(subprocess.check_output(
        ["ip","-j","-4","route","get","1.1.1.1"],timeout=5,text=True))
    if len(route)!=1 or not route[0].get("gateway"):
        raise ValueError("No default gateway available")
    gw=route[0]["gateway"]
    dev=route[0]["dev"]
    gateway=ipaddress.IPv4Address(gw)
    if not gateway.is_private or gateway.is_link_local or gateway.is_loopback:
        raise ValueError("Only a private LAN gateway is eligible")
    links=json.loads(subprocess.check_output(
        ["ip","-j","link","show","dev",dev],timeout=5,text=True))
    addresses=json.loads(subprocess.check_output(
        ["ip","-j","-4","address","show","dev",dev],timeout=5,text=True))
    if len(links)!=1 or len(addresses)!=1:
        raise ValueError("Ambiguous host interface")
    mac=str(links[0].get("address","")).lower()
    ips=[x["local"] for x in addresses[0].get("addr_info",[])
         if x.get("family")=="inet" and x.get("scope")=="global"]
    if len(ips)!=1 or not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}",mac):
        raise ValueError("Ambiguous local IP or MAC")
    return gw,ips[0],mac


def certificate_matches_ip(cert: bytes, ip: str) -> bool:
    # Verify that the certificate's SAN names this *actual default gateway*.
    result=subprocess.run(["openssl","x509","-inform","DER","-noout","-checkip",ip],
                          input=cert,capture_output=True,timeout=4)
    return result.returncode==0


def fetch_authorized(gateway: str) -> dict | None:
    # The Gateway is the current default next hop. TOFU is appropriate only
    # if the local access network prevents ARP spoofing at first enrolment.
    ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname=False
    ctx.verify_mode=ssl.CERT_NONE
    ctx.minimum_version=ssl.TLSVersion.TLSv1_2
    conn=http.client.HTTPSConnection(gateway,PORT,timeout=6,context=ctx)
    try:
        conn.connect()
        cert=conn.sock.getpeercert(binary_form=True)
        if not certificate_matches_ip(cert,gateway):
            raise ValueError("Gateway server certificate does not match its LAN IP")
        fingerprint=hashlib.sha256(cert).hexdigest()
        if PIN.is_symlink():
            raise ValueError("Unsafe TLS fingerprint pin")
        if PIN.exists():
            if PIN.stat().st_uid!=0 or PIN.stat().st_mode & 0o077:
                raise ValueError("Untrusted TLS pin permissions")
            if PIN.read_text().strip()!=gateway+":"+fingerprint:
                raise ValueError("Gateway TLS certificate pin changed unexpectedly")
        else:
            PIN.parent.mkdir(mode=0o750,parents=True,exist_ok=True)
            atomic(PIN,gateway+":"+fingerprint+"\n")
        conn.request("GET",PATH,headers={"Accept":"application/json"})
        response=conn.getresponse()
        if response.status in (403,404):
            return None
        if response.status!=200:
            raise ValueError("Gateway status endpoint unavailable")
        payload=response.read(16385)
        if len(payload)>16384:
            raise ValueError("Gateway response too large")
        result=json.loads(payload)
        if not isinstance(result,dict):
            raise ValueError("Bad Gateway response")
        return result
    finally:
        conn.close()


def atomic(path: Path, content: str):
    if path.is_symlink():
        raise ValueError("Refusing symlink")
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=".gateway-auto-",dir=path.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def observation(snapshot: dict | None, gateway: str, ip: str, mac: str,
                now: float) -> dict:
    result={"schema":1,"port":None,"policy_id":"stremio",
            "registered_mac":mac,"host_ip":ip,"gateway":gateway,
            "route":None,"lease_issued_at":None,"lease_expires_at":None,
            "observed_at":now}
    if snapshot is None:
        return result
    selected=snapshot.get("selected_host") or {}
    runtime=snapshot.get("runtime") or {}
    policy=selected.get("policy_id")
    if (not isinstance(policy,str)
        or not re.fullmatch(r"[a-z][a-z0-9-]{1,31}",policy)
        or selected.get("host_ip")!=ip
        or str(selected.get("registered_mac","")).lower()!=mac
        or runtime.get("owner")!=policy
        or runtime.get("route")!=selected.get("route")
        or not snapshot.get("rules_applied")
        or not runtime.get("applied")):
        return result
    result["policy_id"]=policy
    result["route"]=selected.get("route")
    ports=runtime.get("external_ports") or {}
    tcp,udp=ports.get("tcp"),ports.get("udp")
    if type(tcp)!=int or tcp!=udp or not 1<=tcp<=65535:
        return result
    if result["route"]=="direct" and tcp==6881 and not runtime.get("provider_lease"):
        result["port"]=6881
        return result
    issued,expiry=runtime.get("updated_at"),runtime.get("lease_expires_at")
    if (not isinstance(result["route"],str)
        or not result["route"].startswith("vpn-")
        or not runtime.get("provider_lease")
        or runtime.get("status")!="nat_rules_verified"
        or type(issued) not in (int,float)
        or type(expiry) not in (int,float)
        or not issued<=now<=issued+75
        or expiry-now<12):
        return result
    result.update(port=tcp,lease_issued_at=issued,lease_expires_at=expiry)
    return result


def main():
    # Existing dedicated authenticated watcher remains the preferred authority;
    # never let two observers write the same observation file.
    observer=Path("/etc/mediahub/port-sync.conf")
    if observer.is_file() and any(
        x.strip()=="MEDIAHUB_PORT_SYNC_ENABLE=1" for x in observer.read_text().splitlines()):
        print("dedicated Gateway observer already configured")
        return 0
    gateway,ip,mac=current_identity()
    try:
        snap=fetch_authorized(gateway)
        event=observation(snap,gateway,ip,mac,time.time())
    except (OSError,ValueError,ssl.SSLError,subprocess.SubprocessError) as exc:
        # Never propagate a previously observed VPN port as if still authorised.
        event=observation(None,gateway,ip,mac,time.time())
        print("Gateway automatic observation unavailable:",type(exc).__name__,flush=True)
    # Preserve the actual selected policy for the existing standalone consumer.
    cfg=Path("/etc/stremio-webadmin/port-sync.conf")
    if cfg.is_symlink():
        raise ValueError("Unsafe Stremio port-sync config")
    existing={}
    if cfg.exists():
        for line in cfg.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key,value=line.split("=",1)
                existing[key]=value
    # Only the auto-managed endpoint may change policy identity, and only
    # after the Gateway itself has returned the selected authorised policy.
    if not existing or existing.get("AUTO_MANAGED")=="1":
        policy=event["policy_id"]
        if event["port"] is None and existing.get("POLICY_ID"):
            policy=existing["POLICY_ID"]
        cfg.parent.mkdir(parents=True,exist_ok=True)
        atomic(cfg,f"ENABLED=1\nSOURCE=observation\nAUTO_MANAGED=1\nPOLICY_ID={policy}\nHOST_MAC={mac}\nGATEWAY_IP={gateway}\n")
    atomic(OBS,json.dumps(event,separators=(",",":"))+"\n")
    print("Gateway auto port:",event["port"],flush=True)


if __name__=="__main__":
    main()

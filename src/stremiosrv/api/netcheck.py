"""Inbound-connectivity diagnostic: is the BitTorrent listen port (6881) reachable?

The appliance's whole advantage is *inbound* peering. Behind NAT without a port-forward (or UPnP),
the box reaches only the few publicly-connectable peers -> slow cold-start playback. This endpoint
surfaces the signals the admin page needs to tell the owner whether 6881 is open:

- inboundPeers: peers THEY initiated -> any >0 proves the port is reachable from the internet.
- portMap: whether the router auto-forwarded the port via UPnP/NAT-PMP.
- listenPort / peers: context.
"""
import os

from fastapi import APIRouter, Request

from stremiosrv.torrent.auto_port import desired_port

router = APIRouter()

_CLOSED_PORTMAP = {"mapped": False, "transport": None, "externalPort": None}


def _engine(request: Request):
    return getattr(request.app.state, "engine", None)


@router.get("/netcheck.json")
def netcheck(request: Request) -> dict:
    eng = _engine(request)
    if eng is None:
        return {"listenPort": None, "peers": 0, "inboundPeers": 0, "portMap": dict(_CLOSED_PORTMAP)}
    default_port = int(os.getenv("STREMIOSRV_BT_LISTEN_PORT", "6881"))
    desired, auto_mode = desired_port(default_port)
    actual = eng.listen_port()
    return {
        "listenPort": actual,
        "listenPortMode": auto_mode if actual == desired else "runtime",
        "configuredDefaultPort": default_port,
        "upnpPolicy": "direct-default",
        "upnpEnabled": eng.upnp_enabled(),
        "peers": eng.peer_count(),
        "inboundPeers": eng.inbound_peer_count(),
        "portMap": eng.portmap_status(),
    }

"""Direct-only libtorrent listen-port and local UPnP policy."""
from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)


def desired_port(default_port: int) -> tuple[int, str]:
    port = int(default_port)
    if not 1 <= port <= 65535:
        raise ValueError("invalid BitTorrent listen port")
    return port, "direct"


def run_auto_port_watcher(engine, default_port: int, direct_upnp: bool = True, interval: float = 2.0) -> None:
    """Keep listen port and local router mapping policy stable."""
    port, mode = desired_port(default_port)
    last = None
    while True:
        policy = (port, bool(direct_upnp))
        if policy != last:
            try:
                engine.set_upnp_enabled(bool(direct_upnp))
                engine.set_listen_port(port)
                log.info("BitTorrent direct policy port=%s upnp=%s", port, bool(direct_upnp))
                last = policy
            except Exception as exc:
                log.warning("could not apply direct BitTorrent policy: %s", exc)
        time.sleep(max(0.25, interval))

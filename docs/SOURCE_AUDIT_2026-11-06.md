# Source audit — 2026-11-06

Scope: all tracked StremioServerWebAdmin source, installer, documentation and
regression assets on QA host 192.168.1.199. External VPN Gateway is out of
scope and was not changed.

| Area | Code and protection |
| --- | --- |
| Network | start.sh, sync-host-env.py, sync-host-dns.py; no public DNS fallback |
| GPU | boot-gpu-reconcile.py; Intel render node preserved across recreation |
| BitTorrent | Compose and netcheck; effective TCP/UDP port stays host-owned |
| Gateway | gateway-port-sync.py: only authenticated fresh host assignment counts |
| WebAdmin | webadmin/app.py, static/index.html: separate local port from attestation |
| WebPlayer | patch_continue_watching.py: duration, HLS resume and player lifecycle |
| HLS/IPTV | api/hls.py, converter.py: live MPEG-TS sliding segments and AAC filter |
| Workload | converter.py: stable identity, reaping and bounded failure retries |
| Package | setup script, release builder: .env guard, file bootstrap, SHA-256 |

Consolidation corrections: UI must not misrepresent local port 47883 as
Gateway NAT-PMP assignment; filter aac_adtstoasc only for live IPTV (not VOD);
do not write to Docker's port-status bind path in dpkg postinst; the validated
setup owns creation.

Validated with Python syntax checks, directed regression, full pytest,
package extraction, Docker runtime health and HTTP endpoints. Source archives
exclude host .env and bearer tokens.

Known limits: no dedicated Gateway token/attestation provisioned; images
remain locally built rather than immutable GHCR; real browser IPTV endurance
and second fresh-host install are distinct acceptance criteria. The privileged
unauthenticated WebAdmin must be reachable only from an administrative LAN.

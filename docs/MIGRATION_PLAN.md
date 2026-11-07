# Migration plan: source 3.0.14 -> standalone direct-network

## Audit observations (source `emmanique/stremio-webadmin`)

- `compose.yaml`: Stremio uses `network_mode: service:gluetun`; published ports are on `gluetun`. Pi-hole acts as the stack DNS resolver.
- `webadmin`: `vpn_admin.py`, `vpn_profiles.py`, `gluetun_admin.py`, VPN scripts/UI and VPN-specific API settings.
- `start.sh`: host IPv4 autodiscovery and CPU/VAAPI/NVIDIA detection are reusable, but Pi-hole variables and VPN assumptions need removal.
- `docker/nginx-allinone.conf`: Web Player/API exposed on 8080 and TLS 12470; preserve functionality.
- `src/stremiosrv/torrent/auto_port.py` and associated settings: audit automatic torrent-port forwarding to ensure direct-only logic and no `/vpn/forwarded_port` assumptions.
- CI tests, documentation, release scripts and image naming must be revalidated; deleting services alone is insufficient.

## Architectural contract

- Exactly two application services: `stremio` and `webadmin`; one project-scoped bridge network. Optional GPU overlays do not add application services.
- Expose Web Player HTTP 8080, Stremio API HTTP 11470, HTTPS 12470, BT 6881 TCP/UDP, WebAdmin 8090 on the configured host IPv4.
- `webadmin` connects to `http://stremio:11470`; no gateway container, TUN device, NET_ADMIN or static Docker subnet.
- Host IPv4 is selected using the outbound route or an explicit valid address attached to a host interface; reject loopback, multicast and nonexistent interface address for LAN binds.
- DNS uses the Docker/host resolver policy; no forced `/etc/resolv.conf`, bridge hard-coded DNS server, or Pi-hole.
- Core owns libtorrent direct listening and optional local UPnP/NAT-PMP; no VPN-provider NAT-PMP, VPN-specific forwarded port files, or bypass of host firewall.
- Persistent named volumes for cache, config and WebAdmin data; storage upgrade/rollback documented.
- Docker privileges minimised. WebAdmin lifecycle controller must have bounded restart/recreate permissions; don't ship an unrestricted Docker socket without an explicit threat model.
- TLS certificate behaviour, Stremio client URLs, CORS and Web Player origin must be tested using the actual LAN host IP.

## Implementation tasks

- [x] Preserve source commit SHA, license, attribution; import source tree in `develop`.
- [ ] Replace `compose.yaml`, `compose.vaapi.yaml`, `compose.gpu.yaml` and volume references; choose independent new Compose project name.
- [ ] Clean `start.sh` and `.env.example`; keep host IP and GPU auto-detection.
- [ ] Replace WebAdmin internal API endpoint, status checks and image lifecycle assumptions.
- [ ] Remove VPN controllers, profiles, front-end menus, credentials, translations, static JS and tests.
- [ ] Remove Pi-hole and Gluetun service/image/dependency references and DNS assumptions.
- [ ] Preserve player, HLS, transcode, subtitle and Library features; regression-test them.
- [ ] Replace documentation and release pipelines; build immutable images specific to the new repository.
- [ ] Implement clean install and uninstall preserving user data by default.
- [ ] Add source-scanning and Compose-validation tests prohibiting VPN/Pi-hole and privileged tunnel configuration.
- [ ] Run all unit/integration/functional suites locally, including CPU and GPU paths.
- [ ] Test fresh install, host IP changes, reboot, upgrade and rollback; verify direct egress, DNS resolution and torrent socket TCP/UDP.
- [ ] Promote a reviewed PR from `develop` to `main`; publish v1.0.0 only after acceptance.

## Release acceptance tests

1. `docker compose config --services` contains only `stremio` and `webadmin`.
2. Neither service uses `network_mode: service:...`, `/dev/net/tun`, `NET_ADMIN`, VPN files or custom Pi-hole resolver.
3. Client on the LAN opens the advertised endpoints by host IPv4; the same client can stream, resume and use subtitles.
4. HTTP and HTTPS health checks succeed, preserving certificate trust expectations in client software.
5. DNS and outbound traffic follow normal host routing, with no additional user-network namespace or tunnel.
6. Cache persists across rebuilds, upgrades, restarts and host reboot.
7. WebAdmin remains available during Core restart, and all operational actions have bounded permissions.
8. TCP and UDP BitTorrent bind directly and are reachable only subject to normal LAN/WAN firewall/router configuration.
9. Logs and images contain no VPN secrets, obsolete Gluetun endpoints or hardcoded former host IPs.
10. Package installation pulls only pinned registry digests; no implicit local-only images.

## Scope boundaries

An arbitrary host address cannot be guaranteed to work when the server has multiple NICs, VLANs or NAT; the selected address must be bound locally. Direct internet access does not imply inbound BT port reachability: port forwarding remains dependent on the router and ISP/CGNAT. Publishing WebAdmin ports on a public interface is not permitted by the default deployment policy.

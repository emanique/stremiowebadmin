# VPN Gateway → Stremio port observer: QA verification (2026-11-07)

## Environment
- VPN Gateway host: `192.168.1.231`, authenticated read-only HTTPS endpoint on TCP `9443`.
- Stremio Linux host: `192.168.1.199`, host MAC `bc:24:11:53:5f:65`.
- Active Gateway policy: `stremio`; selected route `vpn-protwg`.
- Current BitTorrent port in Gateway's forwarding mappings: `40819` for **TCP and UDP**.
- Current `STREMIOSRV_BT_LISTEN_PORT` in Stremio host `.env`: `40819`.

## Observed verification
- Authenticated Gateway status request returned HTTP `200`.
- Status exposed `nat_rules_verified` and currently valid lease data.
- Gateway reported `external_reachability_verified: false`; this does **not**
  substantiate peer reachability from the public Internet.
- The bootstrap was downloaded from `https://192.168.1.231:9443/mediahub-sync/v1/bootstrap.sh`
  on the Stremio host with the installed Gateway CA (no insecure TLS bypass).
- `bash -n` and `--verify-only` passed.
- Bundle SHA-256 verification passed for the observer script, unit, timer,
  configuration example and installer (5/5).
- Installed observer script SHA-256 matches the Gateway published manifest:
  `6ed847fd59cc662f26924fbe692a3cdf79fbae4aaa65b993529c3d692a7a97fa`.
- Existing `stremio-webadmin-reconcile.timer` is active; container reported healthy.

## Important activation blocker
- `/etc/mediahub/port-sync.conf` explicitly holds `MEDIAHUB_PORT_SYNC_ENABLE=0`.
- The separate `mediahub-gateway-port-sync.timer` is disabled.
- A dedicated authenticated read-only Gateway bearer token is not present
  at `/etc/mediahub/gateway-port-sync.token`.
- Current reconciliation state: `no_verified_port_change`.
- No token was copied or exposed during QA; a temporary SSH authorization was revoked.
- Therefore a future VPN lease change **has not been proven** to update Stremio
  `.env` or recreate the container automatically.

## Release gate
A system administrator must securely provision a dedicated, least-privilege
status token to the Stremio host via an approved secret-management mechanism,
keeping root ownership and `0600` permissions. Only after verifying the
TLS certificate, policy ID, host MAC, valid lease and route identity should
the observer be enabled.

The remaining acceptance sequence is:
1. Validate authenticated fresh observation and correct timestamp/TTL.
2. Change the VPN lease and confirm observer emission.
3. Verify reconciler updates `.env`, recreates **only Stremio**, checks health
   and preserves VAAPI device assignments and container volumes.
4. Inject failed recreation and verify rollback to the former port and
   healthy state.
5. Restart Stremio host and Gateway and confirm recovery, no DNS leak,
   kill-switch integrity and TCP/UDP mapping.
6. Verify external peer reachability independently; do not infer it from
   local NAT rule application.

The existing operational Stremio service was not upgraded/restarted during
these checks. This is a validated bootstrap and current-state observation,
**not** an end-to-end automatic lease-change acceptance.

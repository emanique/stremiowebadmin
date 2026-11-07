# Gateway observer → Stremio reconciler: QA contract

## Architecture

The VPN Gateway (192.168.1.231) provides an authenticated read-only
port-status API. The separately installed Linux host observer uses the
Gateway's TLS CA and a dedicated bearer token to query that API, verify
the assigned host policy and TCP/UDP ports, and atomically publish a
root-owned `0600` observation at:

`/var/lib/stremio-webadmin/gateway-port-observation.json`

The standalone Stremio host reconciler is **the only actor that changes**
`STREMIOSRV_BT_LISTEN_PORT` in `.env` and recreates the Stremio container.
It no longer fetches credentials or connects directly to the Gateway API.
A static `SOURCE=observation` is mandatory for the enabled path.

## Fail-closed criteria

The reconciler rejects symlinks, absent files, wrong owners, group/world-readable
files and JSON observations exceeding 4096 bytes. Schema must be exactly 1.
The observation policy ID, registered NIC MAC, host IP and currently selected
Gateway address must all match the local configuration and kernel route. A
VPN port must be an integer 1–65535 with an observation no older than 75 seconds,
a recent lease issue timestamp and at least 12 seconds remaining on the lease.
The observer has already verified TCP/UDP equivalence against the authenticated
Gateway response. Direct routing is valid only for port 6881 without provider
lease timestamps. No observer response must cause a guessed port change.

If the requested port matches the Docker container but the host `.env` is
different, reconciliation refuses silent success. Missing/duplicate/invalid
`.env` port configuration is rejected before container modifications.

## Simulated failure injection

- Valid authenticated observation → only Stremio recreation, new `.env` port
- Existing matching port → no recreation
- Docker and `.env` mismatch → fail closed
- Simulated recreation failure → original `.env` restored and former container recreated
- Simulated unhealthy new container → rollback to the original port
- Invalid/expired observation, mismatched policy/MAC/IP/Gateway, unsafe
  filesystem permissions or symlink → never applied
- `SOURCE=direct` mode → explicitly rejected; no direct Gateway API access

Validated 2026-11-07 using frozen Python 3.12/uv.lock dependencies:
- Gateway observation, lease validation, rollback and identity tests: **41 passed**.
- Full frozen Python regression in an isolated DEV worktree:
  **1,624 passed, 2 skipped, 0 failed, 3 warnings**.
- Ruff check on the modified reconciler and its two new tests: PASS.

These tests use a simulated Docker runtime, not an actual provider lease change.

## Operational prerequisites remaining

The current Stremio host .199 has the observer installed but disabled
(`MEDIAHUB_PORT_SYNC_ENABLE=0`), its observer timer is inactive and the
dedicated read-only Gateway token is absent. A previous attempt to transfer an
existing token automatically was blocked and the temporary SSH permissions
were removed. Activation requires an authorised secret-provisioning action.

After authorised provisioning, validate the live sequence:
1. Enable observer, verify TLS/route identity and fresh root-owned observation
2. Change provider allocation; confirm synchronised `.env`, TCP/UDP publishing
   and health of the recreated Stremio container
3. Inject failed recreation; confirm rollback of port, `.env`, container and GPU
4. Reboot the gateway and the Stremio host; verify recovery with no DNS leaks
5. Prove external peer connectivity independently of local NAT-PMP allocation

Do not present this simulation as external reachability or a completed live
lease-change test.

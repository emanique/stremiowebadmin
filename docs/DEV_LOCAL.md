# Local development and security gates

The DEV system is `192.168.1.221` (Debian 13), configured for gateway/DNS
`192.168.1.231`. These are test-host settings, never application defaults.

## Local execution
```bash
cd /opt/stremio-server-webadmin/workspace/StremioServerWebAdmin
sh start.sh                       # host IP detection and local builds
sh start.sh ps
```

The Stremio streaming endpoints bind to the detected host IP:
HTTP 8080 (web player), API 11470, HTTPS 12470 and torrent 6881 TCP/UDP.
They do not traverse VPN or Pi-hole. Docker uses its ordinary resolver.

**The administrative WebAdmin has Docker socket privileges** and no separate
application authentication. It therefore binds to `127.0.0.1:8090` by
default. Do not set `WEBADMIN_BIND_IP=0.0.0.0` or the LAN address without
first implementing and verifying authenticated TLS access and restricting
privileged Docker actions.

To access WebAdmin remotely, create an SSH port forward on the client:
```bash
ssh -N -L 8090:127.0.0.1:8090 user@192.168.1.221
```
Open `http://127.0.0.1:8090` on that client.

## Tests
`/tmp` on this host is a 3.8 GiB tmpfs. Filesystem tests require
at least 10 GiB free as configured in the host reserve policy. Use:
```bash
mkdir -p /opt/stremio-server-webadmin/test-tmp
TMPDIR=/opt/stremio-server-webadmin/test-tmp .venv/bin/pytest -q
```

Don't disable the free-space guard merely to fix tests. Keep local QA
reports outside Git under `/opt/stremio-server-webadmin/reports`.

## Release conditions
Before exposing the WebAdmin to the LAN or promoting to main:
authenticated admin endpoints, hardening of the Docker control interface,
full local suite, functional playback, transcode, subtitles, library/resume,
reboot persistence, image digest and rollback tests are required.

GitHub Actions should only be enabled for final release validation.


## BitTorrent port from VPN Gateway 192.168.1.231

The host-side service \`stremio-webadmin-port-sync.timer\` is responsible for
querying the Gateway's HTTPS \`/api/v1/port-forward/desired\` endpoint with
a dedicated status token, validating the host policy ID/MAC/IP, current route,
fresh lease and identical TCP+UDP external port. The Gateway must explicitly
register and authorise the \`192.168.1.221\` host (MAC \`00:e0:4c:36:0d:26\`).
Do not reuse the allocated port or host policy of \`192.168.1.199\`.

When a *different, verified* port is allocated, the systemd host service
updates \`STREMIOSRV_BT_LISTEN_PORT\` in the private .env and runs
\`docker compose up -d --no-deps --force-recreate stremio-libtorrent-server\`.
This restarts **only Stremio**; WebAdmin is not recreated. It validates
container health and TCP/UDP bindings, and rolls back .env and container
configuration if the transition fails.

Current port, gateway policy state, and lack of authorisation are visible in
WebAdmin's Bt Listen Port description through \`/api/gateway-port\`. Missing
or expired gateway leases never authorize a port update.

Local secrets (Gateway CA and status token) belong under
\`/etc/stremio-webadmin/\` and must never be committed. Sample configuration:
\`config/gateway-port-sync.conf.example\`.

## Host-specific installation

To provision HTTPS on the CURRENT machine (not a fixed development IP), run:

    sudo python3 scripts/configure-host-https.py --apply

The installer detects the IPv4 through the host route, configures Nginx
and uses an IP SAN self-signed certificate. Re-run after an address change.

The .env file owns deployment state such as host IP and published ports.
The WebAdmin JSON stores application preferences separately. Only the
application settings are permitted to overlay Core variables; host-owned
ports and infrastructure paths cannot be overwritten by the JSON file.

On the current dual-GPU reference system, NVIDIA GK208BM (nouveau) is
renderD128, while Intel i915 is renderD129 with iHD. The launcher must choose
Intel by vendor, not by render node order. NVIDIA NVENC remains unavailable
without a supported NVIDIA container runtime and driver.

## Recovery after a kernel upgrade or reboot

GPU render-node numbers can change when DRM modules are loaded in a different
order. Install the optional host unit with:

    sudo cp systemd/stremio-webadmin-boot-reconcile.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now stremio-webadmin-boot-reconcile.service

The unit uses `scripts/boot-gpu-reconcile.py` at startup. It recreates **only
Stremio** if the Intel render node or the TCP/UDP Docker bindings no longer
match the current host. It preserves `.env`, persistent volumes and WebAdmin.

The NVIDIA Tesla 470 driver may be correctly built by DKMS but refused under
UEFI Secure Boot if the DKMS signing key has not been enrolled as a MOK. Do
not disable Secure Boot; enrol the approved MOK through the local boot console.

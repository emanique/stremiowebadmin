# Standalone v0.1.3-rc.1 — Debian 13 clean-install acceptance

Date: 2026-11-07. QA scope: fresh clone, package/build/runtime, admin
security gate, service health and reboot persistence.

## Isolated runner

A disposable Debian GNU/Linux 13 cloud-image VM was started under KVM on
the Debian QA host, using a new 32 GiB CoW disk, 3 GiB memory, 2 vCPUs,
NAT-only networking and SSH bound on the QA host's loopback interface.
No services or VPN policies on `192.168.1.199` or `192.168.1.231`
were changed. The QA VM's IPv4 was `10.0.2.15`.

## Exact distributed source and setup

- Clone: `git clone --branch v0.1.3-rc.1 --depth 1 https://github.com/emanique/stremiowebadmin.git`.
- Source Git commit: `d19807e91d18562d55936da2d6220d2b4f5eee83`.
- Release version: `0.1.3-rc.1`.
- Installed native Debian 13 Docker Engine, Compose v2.26.1, Nginx and dependencies.
- Executed `packaging/stremio-webadmin-setup.sh` as a systemd-managed
  one-shot QA task. It exited successfully (`Result=success`,
  `ExecMainStatus=0`).
- Both Docker images were built from source and public base registries
  in the clean VM; no pre-staged local Docker images or GH Actions used.

## Pass evidence

| Gate | Observed result |
|---|---|
| Core Docker container | `healthy` |
| WebAdmin Docker container | `healthy` |
| Core API `http://10.0.2.15:11470/health` | HTTP `200` |
| HTTPS WebPlayer `https://10.0.2.15:12470/` | HTTP `200` |
| HTTPS WebAdmin anonymous GET | HTTP `401` |
| Admin credentials | Generated once; `webadmin.htpasswd` mode `0640` |
| TLS host certificate | `openssl x509 -checkip 10.0.2.15` matched |
| Default WebAdmin binding | `WEBADMIN_BIND_IP=127.0.0.1` |
| Source repository | `https://github.com/emanique/stremiowebadmin` |
| Host IP in `.env` | Automatically selected `10.0.2.15` |
| Host reconciliation timer | `enabled`, `active` |
| Docker restart policy | `unless-stopped` for both services |

A real OS reboot was then performed inside the QA VM. Both containers
returned to `healthy`; Core API returned HTTP `200`, WebAdmin
anonymous request returned HTTP `401`, and the reconciliation timer
was active. The `.env` SHA-256 remained identical across the reboot:
`63383833944626c137286a581af2b29af0b6a80699b83cef402dcebe538a8ade`.

The valid-credential Nginx authentication path had previously been checked
during the Docker build; it bypassed HTTP 401 and reached a backend-unavailable
HTTP 502. An end-to-end authenticated WebAdmin page request with the fully
running backend has **not** been recorded. No administrator password was
exposed outside the VM.

## Findings and caveats

- The first build downloads a large public FFmpeg/GPU image layer; the
  full runtime installation requires enough network capacity and disk.
- The `uv` build emitted a non-fatal version-constraint warning
  (`uv-build>=0.11.3,<0.13.0` vs newer `uv`).
- This virtualised runner has no physical VAAPI GPU; this test does not
  establish hardware transcoding compatibility.
- IPTV/TV clients, embedded subtitle extraction, dynamic Gateway NAT-PMP
  changes, external peer reachability and failure-injection rollback
  remain separate functional acceptance gates.
- Screenshots have been supplied and sanitised, but are not yet uploaded
  to `docs/screenshots/` in both repositories.

**Conclusion:** clean public clone, complete setup, healthy runtime and
reboot recovery PASS. This alone does not qualify a stable production release.

# StremioServerWebAdmin v0.1.0-rc.1 - QA candidate

Scope: standalone StremioServerWebAdmin. VPN Gateway is not modified.

Component versions: Core 1.6.34; original WebAdmin base 3.0.14.
Distribution: source tarball and Debian source package (.deb), with local Docker image builds.

## Included
- HLS: Core owns copy/transcode decisions; App Direct preserves HTTP Range.
- Browser HEVC Main10 4K H.264 transcode cap at width 1920.
- Continue Watching: full source duration instead of growing HLS window.
- Host IP, DNS, HTTPS and VAAPI render node detection.
- Host-only service/timer for local network and GPU reconciliation and read-only Gateway port polling.
- Gateway status bind file pre-created before Docker Compose.

## QA installation on Debian/Ubuntu

    sudo apt install ./stremio-webadmin_0.1.0~rc1_all.deb
    sudo stremio-webadmin-setup

The setup builds local Docker images, configures HTTPS and enables systemd host reconciliation.
An existing .env is preserved during upgrades.
Do not deploy over an existing application using the same ports without a migration plan.

## Blockers before production
1. Complete live browser Continue Watching, seeking, audio, subtitle, and HLS acceptance.
2. Host-specific Gateway policy/read-only token has not been provisioned on QA .199.
3. GHCR versioned immutable images/digests are not yet published.
4. Fresh-install plus rollback, reboot, upgrade and uninstall scenarios are not yet fully validated.
5. Unauthenticated WebAdmin holds Docker socket privileges; management must be restricted.
6. QA host has 4 GiB RAM; long HEVC/HDR runs need further testing.
7. External Gateway DNS timeouts are not fixed or masked by this project.

This candidate is a QA package, not an approved production release.

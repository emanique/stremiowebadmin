# StremioServerWebAdmin v0.1.0 — source-build release

This is the first independently versioned standalone source-build distribution.
Component baselines: Core 1.6.34; WebAdmin 3.0.14.

## Included fixes

- IPTV MPEG-TS live H.264/AAC remux to bounded fMP4 HLS, without re-encoding.
- AAC ADTS-to-ASC bitstream adaptation limited to live mode (VOD unchanged).
- Workload deduplication, FFmpeg reaping, bounded retry and HLS seek.
- Browser Continue Watching, full source duration and VAAPI render-node handling.
- Host IPv4/DNS reconciliation and preserved BitTorrent TCP/UDP listen port.
- WebAdmin explicitly distinguishes active local port from verified Gateway policy.
- Debian installer preserves .env, protects binding paths and prevents silent port reset.

## Installation

    sudo apt install ./stremio-webadmin_0.1.0_all.deb
    sudo stremio-webadmin-setup

Package contains source code and builds Docker images locally. It does not
publish or pin GHCR image digests. Back up all persistent volumes and .env.

## Acceptance and limitations

Automated regression, package checksum, in-place Debian upgrade and health
checks are required for promotion. External Gateway authenticated NAT-PMP
assignment, second clean host installation and long real-browser IPTV playback
remain independent operational checks; do not claim they were completed by CI.
No code or configuration in the external VPN Gateway is changed.

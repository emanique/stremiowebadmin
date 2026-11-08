# Stremio behind an authenticated WAN Nginx reverse proxy

Operational reference (validated 2026-11-08): [emmanique/stremioproxywan](https://github.com/emanique/stremioproxywan). The WAN proxy is maintained independently of Stremio Core/WebAdmin. **Do not copy its authentication code or TLS secrets into this repository.**

## Client-specific endpoint selection

| Consumer | Endpoint | Authentication / role |
| --- | --- | --- |
| Stremio native App on the LAN | `https://192-168-1-199.519b6502d940.stremio.rocks:12470` | Direct internal HTTPS; select in Settings > Streaming, then Reload |
| Internal browser WebPlayer | `http://192.168.1.199:8080` | Direct LAN access |
| External browser WebPlayer | `https://str.lusosolutions.com` | Authenticated WAN Nginx reverse proxy |

The App must not use `127.0.0.1:11470` unless the Stremio Core runs on the client itself. The externally authenticated URL is not a drop-in substitute for the native App: cookies issued to a browser are not guaranteed to be sent by native streaming clients.

## WAN proxy integration

The proxy serves HTTP 80 to internal port 8080 (inherited route) and HTTPS 443 to the Stremio `stremio.rocks` endpoint 12470, validating the internal certificate. It preserves `Range`/`If-Range`, HLS and proxy-related headers. The external browser first calls `/_session/start` to obtain a signed secure cookie after Basic authentication; anonymous HTTPS requests continue to return 401.

For HLS `mediaURL` parameters containing the external domain, the proxy rewrites the media source to the internal Core port 11470 to prevent an FFmpeg origin loop that would otherwise receive an unauthenticated 401. This applies only to HLS source resolution; ordinary App streaming, native byte-range downloads, IPTV, VPN host policy and torrent ports remain managed by their original components.

## Regression and security boundaries

1. Test direct internal HTTP 8080 and internal HTTPS 12470 independently from the WAN domain.
2. Verify the native App reports Online for the internal HTTPS 12470 and can play an actual title.
3. Verify the external browser loads `master.m3u8`, the secondary playlist, `init.mp4` and `seg*.m4s` and plays a title.
4. Verify anonymous external HTTPS returns 401 and that browser sessions expire.
5. Confirm the VPN Gateway remains owner of host route, DNS and port-forwarding policy; do not change `STREMIOSRV_BT_LISTEN_PORT` because of proxy configuration.
6. Treat port 80 as an independent inherited exposure: it is not secured by Basic/cookie authentication on HTTPS.

Live operational evidence: both native App internal HTTPS and external WebPlayer were confirmed working. The Nginx proxy's source, service, runbook and rollback instructions live exclusively in the WAN proxy repository.

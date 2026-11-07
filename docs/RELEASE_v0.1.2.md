# StremioServerWebAdmin v0.1.2 - Restart race fix

Fixes a false server.restart.failed event when Docker Compose replaces the Core container between a WebAdmin restart request and its health validation.

The worker now resolves the current container by its stable name after restarting; health polling also reacquires the new container after a stale-ID 404. A genuine Docker failure without container replacement remains a failure.

Regression tests cover replacement during restart and during health polling. Existing IP, DNS, BitTorrent configuration and persistent media volumes are preserved. The package builds images locally; no VPN Gateway changes.

# v0.1.1 - Fresh-install compatibility fix

Fixes Docker 29.x lowercase missing-container response during a clean install. The setup correctly identifies an absent Core while preserving failure for Docker-daemon errors. Regression-tested with old and new stderr formats.

The v0.1.0 package has this defect; use v0.1.1 for subsequent fresh installations. Docker images are still compiled locally; no external VPN Gateway changes.

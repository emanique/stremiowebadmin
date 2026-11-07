# Debian/Ubuntu clean-clone dependency bootstrap — QA

Observed failure on the newly restored MediaHub .199 during Stremio public
v0.1.3-rc.3 installation:
`Missing prerequisite: docker`.

The clone itself succeeded and checked out the correct immutable public tag
(commit `4fb1f4ee5381fc99aca79f799de5aefaf397aec5`). The setup had
hard prerequisites, whereas the .deb distribution declared APT Depends.
This was a source-clone packaging inconsistency, not a failure to clone Git.

## Correction in v0.1.3-rc.4

The setup invokes `packaging/stremio-webadmin-prerequisites.sh` before
creating its .env, runtime state or starting any container. The script checks
existing binaries and Docker Compose v2 before making APT changes. On
Debian/Ubuntu it installs only missing packages, considers the distribution's
Compose package naming and verifies `docker compose version` afterwards.
It refuses unsupported OS families, missing systemd and the opt-out case
`STREMIO_SKIP_DEP_INSTALL=1`. No credentials are inserted into Git.

## Verified

- `sh -n` passes on both setup scripts.
- Existing-host bootstrap check returned `Host prerequisites already present`
  on second run (no additional APT changes).
- Targeted dependency and source-contract tests: **8 passed**.
- Full project frozen Python 3.12 regression: **1,627 passed, 3 skipped,
  0 failed**, 3 warnings (331.51 seconds).
- No code changes were deployed to .199 during this validation; the host was
  being manually installed from rc.3 and was temporarily offline in the
  remote management tool.

## Distinction

This establishes the source installer dependency handling. A real clean
install directly from rc.4 is still to be verified on the reset .199, including
Docker images, HTTPS authentication, Gateway integration and reboot.
The previous rc.1 successfully built/reran the full application on a dedicated
Debian 13 KVM QA VM after prerequisite preparation.

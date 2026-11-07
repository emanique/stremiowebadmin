# Development → Public release promotion

## Repositories and authority

- **Private source of truth:** [`emanique/StremioServerWebAdmin`](https://github.com/emanique/StremioServerWebAdmin). All source changes, testing, QA, README edits, screenshot additions, build and release preparation start here.
- **Public distribution:** [`emanique/stremiowebadmin`](https://github.com/emanique/stremiowebadmin). Consumers clone and install from here. **No GitHub Actions or internal CI workflows belong in this repository.**
- [`emmanique/stremio-webadmin`](https://github.com/emmanique/stremio-webadmin) is a **README/layout reference only**, not a code source to merge automatically into the standalone edition.

The public repository must be generated from an **exact, validated private commit**. Public `main` must not independently receive application feature development. Hotfixes begin in DEV and are promoted through the same release gate.

## Version policy

Three different version files are intentional:

| File | Meaning |
|---|---|
| `RELEASE_VERSION` | Standalone distributable version, e.g. `0.1.2` |
| `FORK_VERSION` | WebAdmin/platform version |
| `SERVER_VERSION` | Embedded Stremio core version |

A public tag `v<RELEASE_VERSION>`, signed-off source revision, README, Debian package, source archive and `SHA256SUMS` must all describe the same release. Do not automatically equate the standalone product version with the WebAdmin or core versions.

## Promotion gate (runs locally or in private DEV only)

1. Freeze an exact reviewed private commit on `main` after code and README are aligned. Confirm `git status --porcelain` is empty.
2. Perform source and secret auditing; ensure no host `.env`, credentials, tokens, private keys, database snapshots, caches or test output appear in tracked files or distribution archives.
3. Run the full Python regression suite and static checks with locked dependencies, verify Docker Compose configuration, GPU/VAAPI fallback, WebAdmin/HTTPS, WebPlayer, Continue Watching, HLS/IPTV, BT TCP/UDP ports and the optional external Gateway reconciliation.
4. Test a **fresh clone/install on a second QA host** using the public installation instructions. Verify no requirement for GitHub Actions or pre-built local-only images, and that `stremio-webadmin-setup` builds and starts the expected containers.
5. Run `bash scripts/build-qa-release.sh dist` from the frozen private commit. Verify `(cd dist && sha256sum -c SHA256SUMS)`, inspect `.deb` contents and source archive, and verify no secrets. The package is source-built; it is **not** a GHCR immutable-image release.
6. Promote only the approved tracked product files to the public repository in **one commit**: code, compose, packaging, scripts, documentation, screenshots, tests, licence and provenance. Exclude `.github/workflows/`, environment files, tokens and internal development artefacts.
7. Confirm the public and private `README.md` SHA are identical. Verify all promotable files have matching SHA256 on both exact revisions; compare the tree manifests rather than relying on filenames alone.
8. Build or verify packages against the **public commit**, create public tag `v<RELEASE_VERSION>`, attach the Debian package, source archive, `SHA256SUMS`, release notes and precise source provenance. No release tag should be created before a successful public-clone installation.
9. Keep a record of the private source SHA, public promotion SHA, test results, package hashes and exceptions in the public release notes.

The private repository provides an Actions-free local promotion helper:

```bash
bash scripts/promote-public.sh --dry-run
# Only after QA and review:
bash scripts/promote-public.sh --publish
```

The script does not create a release tag. Release assets and tags remain gated by public-clone QA and explicit verification.

## Screenshot rules

All screenshots are added first under `docs/screenshots/` in DEV. Keep the same filename and gallery ordering in both README files, matching the reference layout. Do not commit placeholder broken `<img>` references. Redact external IP addresses, internal hostnames when necessary, credentials, tokens, user media history and other sensitive identifiers.

## Release blockers (current)

- Second-machine clean installation not yet demonstrated for the first public release.
- Authenticated external Gateway lease **change**, automatic BT port update and rollback after failed recreation need end-to-end verification.
- Extended IPTV playback on real clients remains an acceptance item.
- Source-built local Docker images are supported; immutable published GHCR images are **not** a claimed release property.
- WebAdmin HTTPS Basic authentication has been implemented for new installations; validate its behaviour on clean QA before declaring release acceptance. Restrict administration to a trusted LAN.

Until the above safety and functional gates are satisfied or explicitly documented as accepted limitations, maintain a **release-candidate** status; do not represent a public tag as fully qualified.

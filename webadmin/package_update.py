"""Transactional GHCR package updater for the Stremio runtime.

The server update path is image based: WebAdmin reads the advertised fork version,
pulls the matching immutable GHCR tag, retags it to the configured runtime image,
and reuses the existing transactional activation/rollback logic from fork_update.
No Git checkout or local Docker build is required on the host.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime

import fork_update as source

app = source.app
legacy = source.legacy
SOURCE_REPO = source.SOURCE_REPO
SOURCE_BRANCH = source.SOURCE_BRANCH
PACKAGE_REPO = os.getenv(
    "STREMIO_PACKAGE_REPO",
    "ghcr.io/emmanique/stremio-server",
).rstrip(":/")

fork_status = source.fork_status
_write_result = source._write_result


def _image_tag(image: str) -> str:
    value = image.split("@", 1)[0]
    slash = value.rfind("/")
    colon = value.rfind(":")
    return value[colon + 1 :] if colon > slash else "latest"


def _pull_release_image(version: str) -> dict[str, str]:
    client = legacy.client()
    release_ref = f"{PACKAGE_REPO}:{version}"
    pulled = client.images.pull(PACKAGE_REPO, tag=version)

    labels = pulled.labels or {}
    advertised = labels.get("org.opencontainers.image.version")
    if advertised and advertised != version:
        raise RuntimeError(
            f"package version mismatch: expected {version}, image advertises {advertised}"
        )

    target_repo = source._image_repository(legacy.IMAGE)
    target_tag = _image_tag(legacy.IMAGE)
    pulled.tag(target_repo, tag=target_tag, force=True)

    return {
        "releaseImage": release_ref,
        "runtimeImage": f"{target_repo}:{target_tag}",
        "imageId": pulled.id,
    }


def update_worker():
    started = time.monotonic()
    phase = "starting"
    result = {
        "status": "failed",
        "phase": phase,
        "finishedAt": datetime.now(UTC).isoformat(),
        "repositoryUrl": SOURCE_REPO,
        "branch": SOURCE_BRANCH,
        "packageRepository": PACKAGE_REPO,
    }

    try:
        with legacy.UPDATE_LOCK:
            legacy.STATE.mkdir(parents=True, exist_ok=True)
            version = source._remote_version()
            if not version:
                raise RuntimeError("remote FORK_VERSION is empty")

            installed = source._installed_fork_version()
            if installed == version:
                result = {
                    "status": "succeeded",
                    "phase": "no-op",
                    "finishedAt": datetime.now(UTC).isoformat(),
                    "durationSeconds": round(time.monotonic() - started, 1),
                    "version": version,
                    "repositoryUrl": SOURCE_REPO,
                    "branch": SOURCE_BRANCH,
                    "packageRepository": PACKAGE_REPO,
                    "message": "Server is already at the latest published fork version.",
                }
            else:
                current = legacy.client().containers.get(legacy.CONTAINER)
                current.reload()
                current_image_id = current.image.id
                rollback_image = source._rollback_image()
                current.image.tag(source._image_repository(legacy.IMAGE), tag="rollback", force=True)

                phase = "pulling"
                _write_result(
                    status="running",
                    phase=phase,
                    startedAt=datetime.now(UTC).isoformat(),
                    version=version,
                    previousImageId=current_image_id,
                    rollbackImage=rollback_image,
                    packageRepository=PACKAGE_REPO,
                    message=f"Pulling published runtime package {PACKAGE_REPO}:{version}.",
                )

                package = _pull_release_image(version)

                phase = "activating"
                _write_result(
                    status="running",
                    phase=phase,
                    version=version,
                    packageRepository=PACKAGE_REPO,
                    **package,
                    message="Replacing only the Stremio container and validating health.",
                )
                activation = source._activate_image(version)

                result = {
                    "status": "succeeded",
                    "phase": "committed",
                    "finishedAt": datetime.now(UTC).isoformat(),
                    "durationSeconds": round(time.monotonic() - started, 1),
                    "version": version,
                    "repositoryUrl": SOURCE_REPO,
                    "branch": SOURCE_BRANCH,
                    "packageRepository": PACKAGE_REPO,
                    **package,
                    **activation,
                    "message": (
                        "Published GHCR runtime activated successfully. Stremio passed health "
                        "validation; WebAdmin was not recreated. Previous image "
                        "is retained as rollback."
                    ),
                }
    except Exception as exc:
        result.update(
            {
                "status": "failed",
                "phase": phase,
                "finishedAt": datetime.now(UTC).isoformat(),
                "durationSeconds": round(time.monotonic() - started, 1),
                "message": str(exc),
            }
        )

    _write_result(**result)


# Replace the source-build worker with the published-package worker.
legacy.update_worker = update_worker

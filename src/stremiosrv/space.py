"""Disk-space admission policy for ordinary downloads.

Ordinary downloads are evictable cache and therefore must not use the
much larger Keep/Pin headroom rule.

The server nevertheless keeps an operational/host reserve so downloads
cannot consume the filesystem to exhaustion.
"""

from __future__ import annotations

import math

GIB = 1024 ** 3

DEFAULT_OPERATIONAL_MIN_BYTES = 2 * GIB
DEFAULT_OPERATIONAL_PERCENT = 2.0

DEFAULT_HOST_MIN_FREE_BYTES = 10 * GIB
DEFAULT_HOST_MIN_FREE_PERCENT = 10.0


def reserve_bytes(
    disk_total: int,
    *,
    host_min_free_bytes: int = DEFAULT_HOST_MIN_FREE_BYTES,
    host_min_free_percent: float = DEFAULT_HOST_MIN_FREE_PERCENT,
) -> int:
    """Effective free-space reserve for an ordinary download.

    Preserve the upstream operational floor (2 GiB / 2%) and allow the
    appliance owner to impose a stronger host reserve.
    """
    total = max(0, int(disk_total or 0))
    host_bytes = max(0, int(host_min_free_bytes or 0))
    host_percent = max(0.0, float(host_min_free_percent or 0.0))

    operational_percent = math.ceil(
        total * DEFAULT_OPERATIONAL_PERCENT / 100.0
    )
    host_percent_bytes = math.ceil(
        total * host_percent / 100.0
    )

    return max(
        DEFAULT_OPERATIONAL_MIN_BYTES,
        operational_percent,
        host_bytes,
        host_percent_bytes,
    )


def available_bytes(
    disk_free: int,
    committed: int,
    reserve: int,
) -> int:
    """Bytes still admissible after promises and reserve."""
    return max(
        0,
        int(disk_free or 0)
        - max(0, int(committed or 0))
        - max(0, int(reserve or 0)),
    )


def download_fits(
    disk_free: int,
    disk_total: int,
    committed: int,
    candidate_size: int,
    *,
    host_min_free_bytes: int = DEFAULT_HOST_MIN_FREE_BYTES,
    host_min_free_percent: float = DEFAULT_HOST_MIN_FREE_PERCENT,
) -> bool:
    """True when a new download can finish without entering the reserve."""
    reserve = reserve_bytes(
        disk_total,
        host_min_free_bytes=host_min_free_bytes,
        host_min_free_percent=host_min_free_percent,
    )

    candidate = max(0, int(candidate_size or 0))

    return (
        int(disk_free or 0)
        - max(0, int(committed or 0))
        - candidate
        >= reserve
    )

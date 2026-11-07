from stremiosrv.space import (
    GIB,
    available_bytes,
    download_fits,
    reserve_bytes,
)


def test_default_host_floor_is_ten_gib():
    # 80 GiB disk -> 10% is 8 GiB, so the 10 GiB floor wins.
    assert reserve_bytes(80 * GIB) == 10 * GIB


def test_default_host_percentage_scales_on_large_disk():
    # 200 GiB -> 10% = 20 GiB.
    assert reserve_bytes(200 * GIB) == 20 * GIB


def test_upstream_operational_floor_is_never_lost():
    # Even if the configurable host policy is disabled, retain upstream
    # ordinary-download protection: max(2 GiB, 2% filesystem).
    assert reserve_bytes(
        40 * GIB,
        host_min_free_bytes=0,
        host_min_free_percent=0,
    ) == 2 * GIB


def test_upstream_operational_percentage_is_never_lost():
    # 2% of 200 GiB = 4 GiB, larger than the 2 GiB floor.
    assert reserve_bytes(
        200 * GIB,
        host_min_free_bytes=0,
        host_min_free_percent=0,
    ) == 4 * GIB


def test_committed_space_is_subtracted():
    assert available_bytes(
        disk_free=30 * GIB,
        committed=15 * GIB,
        reserve=10 * GIB,
    ) == 5 * GIB


def test_candidate_that_preserves_reserve_fits():
    assert download_fits(
        disk_free=30 * GIB,
        disk_total=80 * GIB,
        committed=5 * GIB,
        candidate_size=15 * GIB,
    )


def test_candidate_that_enters_reserve_is_rejected():
    assert not download_fits(
        disk_free=30 * GIB,
        disk_total=80 * GIB,
        committed=5 * GIB,
        candidate_size=16 * GIB,
    )


def test_committed_downloads_can_make_new_download_fail():
    assert not download_fits(
        disk_free=30 * GIB,
        disk_total=80 * GIB,
        committed=19 * GIB,
        candidate_size=2 * GIB,
    )


def test_unknown_size_is_allowed_only_outside_reserve():
    assert download_fits(
        disk_free=20 * GIB,
        disk_total=80 * GIB,
        committed=5 * GIB,
        candidate_size=0,
    )

    assert not download_fits(
        disk_free=14 * GIB,
        disk_total=80 * GIB,
        committed=5 * GIB,
        candidate_size=0,
    )


def test_negative_values_cannot_increase_available_space():
    assert available_bytes(
        disk_free=20 * GIB,
        committed=-5,
        reserve=10 * GIB,
    ) == 10 * GIB

"""Adaptive piece-picking: the pure hysteresis law + the Handle tick (fake handle, no libtorrent)."""
from stremiosrv.config import Settings
from stremiosrv.torrent.engine import Handle, adaptive_sequential

MiB = 1024 * 1024
LOW, HIGH = 64 * MiB, 256 * MiB


def test_adaptive_sequential_hysteresis():
    assert adaptive_sequential(300 * MiB, True, LOW, HIGH) is False   # deep buffer -> parallel
    assert adaptive_sequential(10 * MiB, False, LOW, HIGH) is True    # shallow -> in-order (safe)
    assert adaptive_sequential(150 * MiB, True, LOW, HIGH) is True    # between -> hold current
    assert adaptive_sequential(150 * MiB, False, LOW, HIGH) is False  # between -> hold current
    assert adaptive_sequential(150 * MiB, True, 0, 0) is True         # misconfigured -> safe default


class _FakeTI:
    def __init__(self, plen, npieces):
        self._plen, self._n = plen, npieces

    def piece_length(self):
        return self._plen

    def num_pieces(self):
        return self._n


class _FakeHandle:
    def __init__(self, plen, npieces, have):
        self._ti = _FakeTI(plen, npieces)
        self._have = set(have)
        self.seq_calls = []

    def torrent_file(self):
        return self._ti

    def have_piece(self, p):
        return p in self._have

    def set_sequential_download(self, v):
        self.seq_calls.append(v)


def test_adaptive_tick_goes_parallel_when_buffer_deep():
    fh = _FakeHandle(4 * MiB, 400, range(10, 200))  # ~760 MiB contiguous ahead of playhead 10
    h = Handle(fh)
    h._boosted = {10, 11, 12}  # playhead = 10
    assert h.adaptive_tick(LOW, HIGH) is False  # relaxed to parallel
    assert fh.seq_calls == [False]


def test_adaptive_tick_stays_sequential_when_buffer_shallow():
    fh = _FakeHandle(4 * MiB, 400, {10, 11})  # only 8 MiB ahead < LOW
    h = Handle(fh)
    h._boosted = {10}
    assert h.adaptive_tick(LOW, HIGH) is None  # already sequential -> no change
    assert fh.seq_calls == []


def test_adaptive_tick_noop_without_playhead():
    fh = _FakeHandle(4 * MiB, 400, set())
    h = Handle(fh)  # _boosted is empty
    assert h.adaptive_tick(LOW, HIGH) is None
    assert fh.seq_calls == []


def test_config_adaptive_defaults_off():
    s = Settings()
    assert s.adaptive_picking is False
    assert s.adaptive_low_bytes == 67_108_864
    assert s.adaptive_high_bytes == 268_435_456

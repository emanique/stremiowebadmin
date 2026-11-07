import pytest
from stremiosrv.torrent.auto_port import desired_port


def test_direct_port():
    assert desired_port(6881) == (6881, "direct")
    assert desired_port(36131) == (36131, "direct")


@pytest.mark.parametrize("port", [-1, 0, 65536])
def test_reject_invalid_port(port):
    with pytest.raises(ValueError):
        desired_port(port)

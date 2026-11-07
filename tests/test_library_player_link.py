import base64
import json
import zlib
from urllib.parse import unquote

import pytest
from fastapi import HTTPException

from stremiosrv.library.api import PlayerLinkBody, _player_link


def decode_player_link(link: str) -> dict:
    assert link.startswith("/#/player/")
    token = unquote(link.split("/#/player/", 1)[1])
    return json.loads(zlib.decompress(base64.b64decode(token)))


def test_player_link_uses_native_stremio_stream_encoding():
    link = _player_link(PlayerLinkBody(
        infoHash="DD8255ECDC7CA55FB0BBF81323D87062DB1F6D1C",
        fileIdx=7,
        name="Example",
        filename="Example.S01E02.mkv",
    ))
    payload = decode_player_link(link)
    assert payload == {
        "infoHash": "dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c",
        "fileIdx": 7,
        "announce": [],
        "name": "Example",
        "behaviorHints": {"filename": "Example.S01E02.mkv"},
    }


def test_player_link_allows_server_file_guess():
    payload = decode_player_link(_player_link(PlayerLinkBody(
        infoHash="dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c",
        fileIdx=None,
    )))
    assert payload["fileIdx"] is None


@pytest.mark.parametrize("info_hash", ["", "abc", "g" * 40, "../" + "a" * 40])
def test_player_link_rejects_invalid_infohash(info_hash):
    with pytest.raises(HTTPException) as exc:
        _player_link(PlayerLinkBody(infoHash=info_hash))
    assert exc.value.status_code == 400


def test_player_link_rejects_negative_file_index():
    with pytest.raises(HTTPException) as exc:
        _player_link(PlayerLinkBody(
            infoHash="dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c",
            fileIdx=-1,
        ))
    assert exc.value.status_code == 400


def test_player_link_complete_identity_builds_core_player_context():
    link = _player_link(PlayerLinkBody(
        infoHash="dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c",
        fileIdx=7,
        streamTransportUrl="https://streams.example/manifest.json",
        metaTransportUrl="https://meta.example/manifest.json",
        type="series",
        metaId="tt1234567",
        videoId="tt1234567:1:2",
    ))
    parts = link.split("/#/player/", 1)[1].split("/")
    assert len(parts) == 6
    assert unquote(parts[1]) == "https://streams.example/manifest.json"
    assert unquote(parts[2]) == "https://meta.example/manifest.json"
    assert unquote(parts[3]) == "series"
    assert unquote(parts[4]) == "tt1234567"
    assert unquote(parts[5]) == "tt1234567:1:2"


def test_player_link_partial_identity_stays_pathless_instead_of_lying():
    link = _player_link(PlayerLinkBody(
        infoHash="dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c",
        fileIdx=7,
        type="movie",
        metaId="tt1234567",
        videoId="tt1234567",
    ))
    assert len(link.split("/#/player/", 1)[1].split("/")) == 1

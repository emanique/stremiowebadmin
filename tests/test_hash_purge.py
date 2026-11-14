"""Regression tests for source-hash scoped deletion and cache-scanner races."""
import json
import os

import pytest

from stremiosrv import cache as cachemod
from stremiosrv.config import Settings
from stremiosrv.app import create_app
from fastapi.testclient import TestClient

A = "a" * 40
B = "b" * 40


def fixture_cache(root):
    (root / ".resume").mkdir()
    (root / ".subtitle-translator" / A).mkdir(parents=True)
    for name in ("film-a.mkv", "same-hash-alias.mkv", "film-b.mkv"):
        (root / name).write_bytes(b"movie")
    (root / f".{A}.parts").write_bytes(b"partial")
    (root / ".resume" / f"{A}.fastresume").write_bytes(b"resume")
    (root / ".subtitle-translator" / A / "translated.srt").write_text("ola")
    (root / ".subtitle-translator" / A / "translated.json").write_text("{}")
    (root / ".resume" / "index.json").write_text(json.dumps(
        {"film-a.mkv": A, "same-hash-alias.mkv": A, "film-b.mkv": B}))
    (root / "labels.json").write_text(json.dumps({A: {"metaId":"tt1"},B: {"metaId":"tt2"}}))
    (root / "wanted.json").write_text(json.dumps({A: [{}], B: [{}]}))
    (root / "pins.json").write_text(json.dumps(
        [{"infoHash": A, "name":"film-a.mkv"}, {"infoHash": B, "name":"film-b.mkv"}]))


def test_purge_removes_every_hash_association_and_preserves_unrelated(tmp_path):
    fixture_cache(tmp_path)
    outcome = cachemod.purge_title(str(tmp_path), A)
    assert outcome["removed"]
    assert sorted(outcome["names"]) == ["film-a.mkv", "same-hash-alias.mkv"]
    for name in ("film-a.mkv","same-hash-alias.mkv",f".{A}.parts"):
        assert not (tmp_path / name).exists()
    assert not (tmp_path / ".resume" / f"{A}.fastresume").exists()
    assert not (tmp_path / ".subtitle-translator" / A).exists()
    assert (tmp_path / "film-b.mkv").is_file()
    assert json.loads((tmp_path / "labels.json").read_text()) == {B: {"metaId":"tt2"}}
    assert json.loads((tmp_path / ".resume" / "index.json").read_text()) == {"film-b.mkv":B}
    assert B in json.loads((tmp_path / "wanted.json").read_text())
    assert A not in json.loads((tmp_path / "wanted.json").read_text())
    assert [r["infoHash"] for r in json.loads((tmp_path / "pins.json").read_text())] == [B]
    assert cachemod.purge_title(str(tmp_path), A)["names"] == []


def test_remove_cache_endpoint_purges_all_aliases(tmp_path):
    fixture_cache(tmp_path)
    c=TestClient(create_app(settings=Settings(cache_root=str(tmp_path))))
    result=c.post("/cache/remove",json={"name":"film-a.mkv"})
    assert result.status_code==200
    assert not (tmp_path / "same-hash-alias.mkv").exists()
    assert (tmp_path / "film-b.mkv").exists()
    assert A not in json.loads((tmp_path / "labels.json").read_text())


def test_purge_rejects_bad_hash_and_does_not_delete_symlink(tmp_path):
    fixture_cache(tmp_path)
    with pytest.raises(ValueError):
        cachemod.purge_title(str(tmp_path), "../")
    (tmp_path / "outside").write_text("preserve")
    (tmp_path / "alias-link").symlink_to(tmp_path / "outside")
    idx=cachemod.load_name_index(str(tmp_path))
    idx["alias-link"]=A
    cachemod.save_name_index(str(tmp_path),idx)
    cachemod.purge_title(str(tmp_path),A)
    assert (tmp_path / "outside").read_text()=="preserve"


def test_scan_cache_survives_concurrent_temporary_file_removal(tmp_path, monkeypatch):
    path=tmp_path / "labels.json.999.tmp"
    path.write_text("temporary")
    original_stat=os.stat
    original_isfile=os.path.isfile
    monkeypatch.setattr(os.path,"isfile",lambda p: True if str(p)==str(path) else original_isfile(p))
    monkeypatch.setattr(os,"stat",lambda p,*a,**kw: (_ for _ in ()).throw(FileNotFoundError(str(p))) if str(p)==str(path) else original_stat(p,*a,**kw))
    assert cachemod._stat_tree(str(path))==(0,0.0)


def test_evict_title_removes_related_hash_metadata(tmp_path):
    fixture_cache(tmp_path)
    import time
    old=time.time()-10_000
    for n in ("film-a.mkv", "same-hash-alias.mkv"):
        os.utime(tmp_path / n, (old, old))
    # Pins must not be evicted; this scenario tests an ordinary, unpinned title.
    (tmp_path / "pins.json").write_text(json.dumps(
        [{"infoHash": B, "name": "film-b.mkv"}]))
    # The unrelated file is fresh, so it must survive an over-budget pass.
    result=cachemod.evict_once(str(tmp_path),budget=2,engine=None,grace=300)
    assert result["deleted"]
    assert not (tmp_path / "film-a.mkv").exists()
    assert not (tmp_path / "same-hash-alias.mkv").exists()
    assert (tmp_path / "film-b.mkv").exists()
    assert A not in json.loads((tmp_path / "labels.json").read_text())

def test_explicit_hash_purge_route_removes_every_alias(tmp_path):
    fixture_cache(tmp_path)
    c = TestClient(create_app(settings=Settings(cache_root=str(tmp_path))))
    assert c.post("/cache/purge/not-a-hash").status_code == 400
    r = c.post("/cache/purge/" + A.upper())
    assert r.status_code == 200, r.text
    assert r.json()["removed"]
    assert not (tmp_path / "film-a.mkv").exists()
    assert not (tmp_path / "same-hash-alias.mkv").exists()
    assert (tmp_path / "film-b.mkv").exists()


def test_hash_purge_uses_learned_file_when_index_absent(tmp_path):
    fixture_cache(tmp_path)
    cachemod.save_name_index(str(tmp_path), {"film-b.mkv": B})
    rows = json.loads((tmp_path / "labels.json").read_text())
    rows[A]["file"] = {"name": "film-a.mkv", "size": 5}
    (tmp_path / "labels.json").write_text(json.dumps(rows))
    assert cachemod.purge_title(str(tmp_path), A)["removed"]
    assert not (tmp_path / "film-a.mkv").exists()
    assert (tmp_path / "film-b.mkv").exists()

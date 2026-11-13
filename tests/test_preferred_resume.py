"""Continue Watching preferred-source server verification."""
from __future__ import annotations
import base64,json,zlib
from types import SimpleNamespace
from urllib.parse import unquote
import pytest
from fastapi import HTTPException
from stremiosrv.library import api,labels

HASH='9597e3af6de9d6fe45def98daad1352cc44283af'
TITLE='tt35511966'
FILENAME='La bola negra.mkv'


def _fixture(monkeypatch,tmp_path):
    row={'metaId':TITLE,'type':'movie','file':{'name':FILENAME,'size':1024},'preferredFileIdx':0}
    labels._save(str(tmp_path),{HASH:row})
    entry={'infoHash':HASH,'kind':'download','name':FILENAME,'numFiles':1,
           'files':[{'index':0,'name':FILENAME,'size':1024,'downloaded':1024,'progress':1}],
           'label':row}
    monkeypatch.setattr(api.stremio_api,'get_user',lambda k:{'_id':'user-test'} if k=='valid' else {'_id':'other'})
    monkeypatch.setattr(api.sessionmod,'load_state',lambda root:{'owner_id':'user-test'})
    monkeypatch.setattr(api.sessionmod,'ensure_addon_token',lambda root:'private-token')
    monkeypatch.setattr(api.statemod,'build',lambda *a,**k:{'entries':[entry]})
    monkeypatch.setattr(api.netguard,'is_allowed',lambda *a:True)
    settings=SimpleNamespace(cache_root=str(tmp_path),library_owner='',library_addon_allow='',cache_size=0)
    request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings,engine=None)),
        client=SimpleNamespace(host='192.168.1.40'),headers={'host':'192.168.1.200:12470'},
        method='POST',url=SimpleNamespace(scheme='https',netloc='192.168.1.200:12470'))
    return row,entry,request


def test_preferred_single_movie_resumes_with_same_hash(monkeypatch,tmp_path):
    row,entry,request=_fixture(monkeypatch,tmp_path)
    result=api.preferred_resume(api.PreferredResumeBody(authKey='valid',metaId=TITLE),request)
    assert result['preferred'] is True and '/#/player/' in result['url']
    params=result['url'].split('/#/player/')[1].split('/')
    stream=json.loads(zlib.decompress(base64.b64decode(unquote(params[0]))))
    assert stream['infoHash']==HASH and stream['fileIdx']==0
    assert unquote(params[4])==TITLE and unquote(params[5])==TITLE


def test_preferred_falls_back_on_missing_incomplete_wrong_title_and_pack(monkeypatch,tmp_path):
    row,entry,request=_fixture(monkeypatch,tmp_path)
    body=api.PreferredResumeBody(authKey='valid',metaId=TITLE)
    assert api.preferred_resume(api.PreferredResumeBody(authKey='valid',metaId='tt99999'),request)=={'preferred':False}
    del row['preferredFileIdx']
    assert api.preferred_resume(body,request)=={'preferred':False}
    row['preferredFileIdx']=0
    entry['files'][0]['downloaded']=256
    entry['files'][0]['progress']=0.25
    assert api.preferred_resume(body,request)=={'preferred':False}
    entry['files'][0]['downloaded']=1024
    entry['files'][0]['progress']=1.0
    entry['numFiles']=3
    assert api.preferred_resume(body,request)=={'preferred':False}


def test_preferred_rejects_other_account(monkeypatch,tmp_path):
    _,_,request=_fixture(monkeypatch,tmp_path)
    with pytest.raises(HTTPException) as exc:
        api.preferred_resume(api.PreferredResumeBody(authKey='other',metaId=TITLE),request)
    assert exc.value.status_code==403

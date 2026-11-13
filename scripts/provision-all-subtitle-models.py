#!/usr/bin/env python3
"""Optional Argos CTranslate2 source→English model provisioning for pt-PT.

Fetches the public Argos catalogue and compatible (model.bin, config.json,
sentencepiece.model) models. Never writes credentials or application state,
never starts Docker, never removes existing model directories. Requires the
three SHA-256 pinned baseline models from provision-subtitle-models.sh first.

CAUTION: Online catalogue/model packages can change; for highly-controlled
installations stage pre-audited archives and verify their checksums offline.
"""
from __future__ import annotations
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import urllib.request
import zipfile

INDEX='https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json'
PAIR_RE=re.compile(r'^[a-z]{2}_en$')
NEEDED=('model/model.bin','model/config.json','sentencepiece.model')
MAX_DOWNLOAD=450*1024*1024


def fetch_index():
    req=urllib.request.Request(INDEX,headers={'User-Agent':'StremioModelProvisioner/1.0'})
    with urllib.request.urlopen(req,timeout=30) as source:
        data=json.load(source)
    if not isinstance(data,list):
        raise ValueError('invalid catalogue')
    return {x['from_code']:x for x in data
            if isinstance(x,dict) and x.get('to_code')=='en' and
            isinstance(x.get('from_code'),str) and
            re.fullmatch(r'[a-z]{2}',x['from_code']) and x.get('links')}


def installed(dest: Path):
    return (dest.joinpath(*('model/model.bin'.split('/'))).is_file() and
            (dest/'model/config.json').is_file() and
            (dest/'sentencepiece.model').is_file())


def install_model(root:Path, temp_root:Path, code:str,info:dict):
    pair=code+'_en'
    if not PAIR_RE.fullmatch(pair):return (pair,'rejected')
    final=root/pair
    if installed(final):return (pair,'present')
    links=[u for u in info.get('links',[]) if isinstance(u,str) and u.startswith('https://')]
    if not links:return (pair,'unavailable')
    # Official catalogue package reference; do not follow arbitrary HTTP URLs.
    urls=[]
    for url in links:
        filename=url.rsplit('/',1)[-1]
        if re.fullmatch(r'translate-[a-z]{2}_en-[0-9_]+\.argosmodel',filename):
            urls.extend(('https://data.argosopentech.com/argospm/v1/'+filename,url))
    urls=list(dict.fromkeys(urls))
    error='unavailable'
    for url in urls:
        try:
            with tempfile.TemporaryDirectory(prefix=pair+'-',dir=temp_root) as td:
                td=Path(td);archive=td/'model.argosmodel'
                request=urllib.request.Request(url,headers={'User-Agent':'StremioModelProvisioner/1.0'})
                digest=hashlib.sha256();count=0
                with urllib.request.urlopen(request,timeout=65) as inp,archive.open('wb') as out:
                    while block:=inp.read(1024*1024):
                        count+=len(block)
                        if count>MAX_DOWNLOAD:raise ValueError('model archive too large')
                        digest.update(block);out.write(block)
                with zipfile.ZipFile(archive) as z:
                    files={x.filename.split('/',1)[-1]:x for x in z.infolist() if '/' in x.filename}
                    if any(key not in files for key in NEEDED):
                        raise ValueError('non-CTranslate2 legacy package')
                    stage=td/pair;stage.mkdir()
                    for key in (*NEEDED,'model/shared_vocabulary.json'):
                        entry=files.get(key)
                        if entry and not entry.is_dir():
                            target=stage/key;target.parent.mkdir(parents=True,exist_ok=True)
                            with z.open(entry) as inp,target.open('wb') as out:shutil.copyfileobj(inp,out)
                    if not installed(stage):raise ValueError('incomplete package')
                    (stage/'SOURCE_SHA256.txt').write_text(digest.hexdigest()+'  '+url.rsplit('/',1)[-1]+'\n')
                    if not final.exists():os.rename(stage,final)
                return (pair,'installed')
        except (OSError,ValueError,zipfile.BadZipFile) as ex:
            error=type(ex).__name__
    return (pair,'skipped: '+error)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--install',action='store_true',help='download compatible models (default: list only)')
    ap.add_argument('--root',type=Path,default=Path(os.getenv('STREMIOSRV_SUBTITLE_MODELS_DIR','models/subtitles')))
    ap.add_argument('--workers',type=int,default=3)
    args=ap.parse_args()
    root=args.root.resolve()
    catalogue=fetch_index()
    if not args.install:
        print('Catalogue source→English pairs:',len(catalogue))
        print('Detected models:',sum(installed(root/(x+'_en')) for x in catalogue))
        print('Compatible binaries must be verified before they are advertised by the addon.')
        return
    if not all(installed(root/name) for name in ('en_pt','es_pt','fr_en')):
        raise SystemExit('Run scripts/provision-subtitle-models.sh --download-and-install first')
    root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='stremio-models-',dir=str(root)) as temporary:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1,min(args.workers,4))) as pool:
            jobs={pool.submit(install_model,root,Path(temporary),src,catalogue[src]):src for src in sorted(catalogue)}
            for job in concurrent.futures.as_completed(jobs):
                pair,status=job.result();print(pair,status,flush=True)
    print('Ready source languages depend on valid installed CTranslate2 binaries, not catalogue entries.')


if __name__=='__main__':main()

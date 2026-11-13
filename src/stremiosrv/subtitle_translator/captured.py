"""Best-effort capture of subtitle bodies *already fetched* by Stremio Core.

Never blocks or alters the existing subtitle response. Never guesses which title
owns a subtitle when playback activity is ambiguous. Raw original subtitles
remain distinct from translated SRT and transcoding files.
"""
from __future__ import annotations
import hashlib,json,logging,re
from pathlib import Path
from urllib.parse import urlsplit

from stremiosrv import cache
from stremiosrv.subtitle_translator.normalize import to_srt
from stremiosrv.subtitle_translator.sources import normalize_language
from stremiosrv.subtitle_translator.local_engine import supported_source_languages
from stremiosrv.subtitle_translator import _atomic

log=logging.getLogger(__name__)
MAX_SIZE=4*1024*1024
_KEY=re.compile(r'^[a-f0-9]{64}$')
_HASH=re.compile(r'^[a-f0-9]{40}$')
_TIME=re.compile(r'^\s*(?:\d+|\d\d:\d\d:\d\d[,.]\d{3}\s+-->.*)$',re.M)


def unique_playing_hash(request)->str|None:
    registry=getattr(request.app.state,'playback_registry',None)
    if registry is None:return None
    try:
        active=registry.snapshot().get('active',[])
        titles={item['infoHash'] for item in active
                if item.get('infoHash') and item.get('client') != 'ffmpeg'
                and item.get('kind') in ('source','hls')}
        if len(titles)!=1:return None
        ih=next(iter(titles))
        if not _HASH.fullmatch(ih):return None
        root=request.app.state.settings.cache_root
        return ih if any(v==ih and (Path(root)/k).exists()
                  for k,v in cache.load_name_index(root).items()
                  if k==Path(k).name and k not in cache.PROTECTED) else None
    except (AttributeError,KeyError,OSError,ValueError):return None


def capture(root:str,ih:str,source_url:str,body:bytes)->None:
    """Background-only action. Uncertain-language sources never advertised."""
    if not _HASH.fullmatch(ih) or not 20<len(body)<=MAX_SIZE:return
    parsed=urlsplit(source_url)
    if parsed.scheme!='https' or not parsed.hostname:return
    try:
        srt=to_srt(body)
        if '-->' not in srt:return
        # Strip timecodes, indexes and format tags before detection.
        sample=_TIME.sub('',srt)
        sample=re.sub(r'<[^>]*>|\{\\[^}]*\}', '',sample)
        sample=re.sub(r'\b\d+\b',' ',sample)
        if len(sample.strip())<200:return
        from langdetect import detect_langs,DetectorFactory
        DetectorFactory.seed=0
        guess=detect_langs(sample[:12000])
        if not guess or guess[0].prob<0.90:return
        lang=normalize_language(guess[0].lang)
        if not lang or lang not in supported_source_languages():return
        folder=Path(root)/'.subtitle-translator'/ih
        # Never recreate a cache entry for a removed title.
        if not any(v==ih and (Path(root)/k).exists() for k,v in cache.load_name_index(root).items()
                   if k==Path(k).name and k not in cache.PROTECTED):return
        # Different subtitle texts served by the same URL are distinct candidates.
        # A repeated identical selection is deduplicated by source+content.
        name=hashlib.sha256((source_url+'\n'+lang+'\n'+hashlib.sha256(srt.encode('utf-8')).hexdigest()).encode()).hexdigest()
        if not _KEY.fullmatch(name):return
        folder.mkdir(parents=True,exist_ok=True)
        source_file=folder/(name+'.source.srt')
        meta=folder/(name+'.source.json')
        if not source_file.is_file():_atomic(source_file,srt)
        if not meta.is_file():
            # Human-readable, stable variant label; do not persist the source
            # URL, which may carry access tokens or other private query data.
            candidate=Path(parsed.path).name or 'legenda'
            label=re.sub(r'[^a-zA-Z0-9._ -]', '',candidate)[:65] or 'legenda'
            variant='Fonte '+name[:8]+' · '+label
            _atomic(meta,json.dumps({'id':name,'sourceLanguage':lang,
                      'sourceAddon':parsed.hostname[:90],'name':variant,
                      'file':source_file.name},ensure_ascii=False))
        log.info('subtitle captured for cache title %s, language %s',ih[:8],lang)
    except Exception as exc:
        # Never affect original subtitle delivery on any capture error.
        log.debug('subtitle capture skipped: %s',type(exc).__name__)


def sources_for_title(root:str,ih:str)->list[dict]:
    if not _HASH.fullmatch(ih):return []
    folder=Path(root)/'.subtitle-translator'/ih
    if not folder.is_dir() or folder.is_symlink():return []
    options=[]
    for p in sorted(folder.glob('*.source.json'))[:80]:
        try:
            if p.is_symlink() or p.stat().st_size>4096:continue
            meta=json.loads(p.read_text('utf-8'))
            key=meta.get('id');lang=meta.get('sourceLanguage')
            if not isinstance(key,str) or not _KEY.fullmatch(key):continue
            if lang not in supported_source_languages():continue
            original=folder/(key+'.source.srt')
            if not original.is_file() or original.is_symlink() or original.stat().st_size>MAX_SIZE:continue
            options.append({'id':key,'sourceLanguage':lang,'sourceAddon':'Stremio · '+meta.get('sourceAddon','Legendas'),
                            'name':meta.get('name','Legenda capturada'),'kind':'captured','path':original})
        except (OSError,ValueError,TypeError,KeyError):continue
    return options

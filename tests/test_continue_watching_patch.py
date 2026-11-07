"""Regression tests for the bundled Stremio Web Continue Watching patch."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "docker" / "patch_continue_watching.py"
_SPEC = importlib.util.spec_from_file_location("patch_continue_watching", _PATH)
_MOD = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_MOD)


def test_patch_only_targets_centre_play_inside_continue_watching():
    source = 'x=e?"string"==typeof e.metaDetailsStreams?e.metaDetailsStreams:"string"==typeof e.player?e.player:null:null;'
    patched, status = _MOD.patch_text(source)
    assert status == 1
    assert patched.startswith(source)
    assert patched.count(source) == 1
    assert _MOD.MARKER in patched
    assert 'play-icon-layer' in patched
    assert 'poster-change-cursor' in patched
    assert 'e.selectPrevented=true' in patched
    assert 'preventDefault' not in patched
    assert 'stopImmediatePropagation' not in patched
    assert 'dispatchEvent' not in patched


def test_patch_leaves_poster_and_details_navigation_alone():
    patched, status = _MOD.patch_text("bundle;")
    assert status == 1
    assert 'const play=t.closest(\'[class*="play-icon-layer"]\');if(!play)return;' in patched
    assert 'const poster=play.closest(\'[class*="poster-change-cursor"]\');if(!poster)return;' in patched


def test_patch_is_idempotent():
    once, status = _MOD.patch_text("bundle;")
    assert status == 1
    twice, status = _MOD.patch_text(once)
    assert status == 0
    assert twice == once


def test_player_unload_preserves_last_time_and_emits_cleanup_event():
    source = 'case"unload":O=null,s.removeAttribute("src"),s.load(),s.currentTime=0,D("stream"),D("time");break;'
    patched, count = _MOD.patch_player_unload(source)
    assert count == 1
    assert _MOD.UNLOAD_EVENT in patched
    assert 's.currentTime=0' not in patched
    assert patched.index(_MOD.UNLOAD_EVENT) < patched.index('s.removeAttribute("src")')
    assert 'D("time")' in patched


def test_player_unload_patch_does_not_touch_hls_internal_detach():
    source = 'e.removeAttribute("src"),jE(e),e.load()'
    patched, count = _MOD.patch_player_unload(source)
    assert count == 0
    assert patched == source


def test_player_unload_patch_is_idempotent():
    source = 'v.removeAttribute("src"),v.load(),v.currentTime=0,P("time")'
    once, count = _MOD.patch_player_unload(source)
    assert count == 1
    twice, count = _MOD.patch_player_unload(once)
    assert count == 0
    assert twice == once


def test_initial_resume_is_reapplied_after_metadata():
    source = 's.autoplay="boolean"!=typeof i.autoplay||i.autoplay,s.currentTime=null!==i.time&&isFinite(i.time)?parseInt(i.time,10)/1e3:0,s.src=O.url'
    patched, count = _MOD.patch_initial_resume(source)
    assert count == 1
    assert 's.__stremioResumeTime=' in patched
    assert 'addEventListener("canplay"' in patched
    assert 's.currentTime<1&&(s.currentTime=t)' in patched
    assert patched.endswith('s.src=O.url')


def test_initial_resume_patch_handles_multiple_html5_players():
    source = ';'.join([
        's.autoplay="boolean"!=typeof i.autoplay||i.autoplay,s.currentTime=null!==i.time&&isFinite(i.time)?parseInt(i.time,10)/1e3:0,',
        'A.autoplay="boolean"!=typeof t.autoplay||t.autoplay,A.currentTime=null!==t.time&&isFinite(t.time)?parseInt(t.time,10)/1e3:0,',
        'a.autoplay="boolean"!=typeof i.autoplay||i.autoplay,a.currentTime=null!==i.time&&isFinite(i.time)?parseInt(i.time,10)/1e3:0,',
    ])
    patched, count = _MOD.patch_initial_resume(source)
    assert count == 3
    assert patched.count('__stremioResumeTime=') == 3


def test_initial_resume_patch_is_idempotent():
    source = 's.autoplay="boolean"!=typeof i.autoplay||i.autoplay,s.currentTime=null!==i.time&&isFinite(i.time)?parseInt(i.time,10)/1e3:0,'
    once, count = _MOD.patch_initial_resume(source)
    assert count == 1
    twice, count = _MOD.patch_initial_resume(once)
    assert count == 0
    assert twice == once


def test_hls_resume_query_forwards_core_time():
    source = 'var t=E(),r=new URLSearchParams([["mediaURL",a]]);return i.forceTranscoding&&r.set("forceTranscoding","1"),S.forEach(function(e){r.append("videoCodecs",e)})'
    patched, count = _MOD.patch_hls_resume_query(source)
    assert count == 1
    assert 'r.set("startTime",Math.max(0,Math.round(i.time)))' in patched
    assert 'i.time>0' in patched


def test_hls_resume_query_is_idempotent():
    source = 'var t=E(),r=new URLSearchParams([["mediaURL",a]]);return i.forceTranscoding&&r.set("forceTranscoding","1"),'
    once, count = _MOD.patch_hls_resume_query(source)
    assert count == 1
    twice, count = _MOD.patch_hls_resume_query(once)
    assert count == 0
    assert twice == once


def test_hls_resume_timeline_maps_local_media_time_to_core_time():
    source = (
        'var A=this,u=null,O=!1,d=[],N=null,R=new i,c=!1,m={stream:!1,videoParams:!1};'
        'function L(e,t,a){R.emit(e,t,p(t,a))}'
        'l.dispatch({type:"command",commandName:"load",commandArgs:Object.assign({},i,{stream:t.stream})})'
        'case"unload":return u=null,O=!1,d=[],N=null,D("stream"),D("videoParams"),!1;'
    )
    patched, count = _MOD.patch_hls_resume_timeline(source)
    assert count == 1
    assert '__stremioHlsResumeOffset' in patched
    assert '"time"===t' in patched
    assert '"duration"===t' in patched
    assert 't.stream.url.indexOf("/hlsv2/")' in patched
    assert 'time:__stremioHlsResumeOffset>0?0:i.time' in patched
    assert '__stremioHlsResumeOffset=0,D("stream")' in patched


def test_hls_resume_timeline_preserves_direct_stream_resume_time():
    source = (
        'var A=this,u=null,O=!1,d=[],N=null,R=new i,c=!1,m={stream:!1,videoParams:!1};'
        'function L(e,t,a){R.emit(e,t,p(t,a))}'
        'l.dispatch({type:"command",commandName:"load",commandArgs:Object.assign({},i,{stream:t.stream})})'
        'case"unload":return u=null,O=!1,d=[],N=null,D("stream"),D("videoParams"),!1;'
    )
    patched, count = _MOD.patch_hls_resume_timeline(source)
    assert count == 1
    # Only HLS v2 streams may consume Core resume time server-side.
    assert '-1!==t.stream.url.indexOf("/hlsv2/")' in patched
    # Direct streams retain Core's original time instead of being forced to zero.
    assert 'time:__stremioHlsResumeOffset>0?0:i.time' in patched


def test_hls_resume_timeline_is_idempotent():
    source = (
        'var A=this,u=null,O=!1,d=[],N=null,R=new i,c=!1,m={stream:!1,videoParams:!1};'
        'function L(e,t,a){R.emit(e,t,p(t,a))}'
        'l.dispatch({type:"command",commandName:"load",commandArgs:Object.assign({},i,{stream:t.stream})})'
        'case"unload":return u=null,O=!1,d=[],N=null,D("stream"),D("videoParams"),!1;'
    )
    once, count = _MOD.patch_hls_resume_timeline(source)
    assert count == 1
    twice, count = _MOD.patch_hls_resume_timeline(once)
    assert count == 0
    assert twice == once


def test_external_subtitle_resume_query_patches_both_paths():
    needle = 'new URLSearchParams([["from",e.url]]).toString()'
    source = needle + "..." + needle
    patched, count = _MOD.patch_external_subtitle_resume_query(source)
    assert count == 2
    assert patched.count('["startTime",String(__stremioHlsResumeOffset)]') == 2


def test_external_subtitle_resume_query_is_idempotent():
    needle = 'new URLSearchParams([["from",e.url]]).toString()'
    source = needle + "..." + needle
    once, count = _MOD.patch_external_subtitle_resume_query(source)
    assert count == 2
    twice, count = _MOD.patch_external_subtitle_resume_query(once)
    assert count == 0
    assert twice == once

def test_resume_patches_keep_hls_server_offset_and_html5_resume_independent():
    """HLS consumes Core time server-side; HTML5/direct re-applies Core time after metadata."""
    html5 = 's.autoplay="boolean"!=typeof i.autoplay||i.autoplay,s.currentTime=null!==i.time&&isFinite(i.time)?parseInt(i.time,10)/1e3:0,'
    hls = (
        'var A=this,u=null,O=!1,d=[],N=null,R=new i,c=!1,m={stream:!1,videoParams:!1};'
        'function L(e,t,a){R.emit(e,t,p(t,a))}'
        'l.dispatch({type:"command",commandName:"load",commandArgs:Object.assign({},i,{stream:t.stream})})'
        'case"unload":return u=null,O=!1,d=[],N=null,D("stream"),D("videoParams"),!1;'
    )
    patched, n = _MOD.patch_hls_resume_timeline(hls + html5)
    assert n == 1
    patched, n = _MOD.patch_initial_resume(patched)
    assert n == 1
    assert 'time:__stremioHlsResumeOffset>0?0:i.time' in patched
    assert 's.__stremioResumeTime=' in patched
    # HLS hands zero to HTML5, so the HTML5 canplay repair cannot double-apply the HLS offset.
    assert '__stremioHlsResumeOffset>0?0:i.time' in patched


def test_hls_autoplay_retries_after_manifest_parsed():
    source = 'I.loadSource(O.url),I.attachMedia(s)'
    patched, count = _MOD.patch_hls_autoplay_after_attach(source)
    assert count == 1
    assert 'Events.MANIFEST_PARSED' in patched
    assert 'i.autoplay' in patched
    assert 's.play().catch(function(){})' in patched

def test_hls_autoplay_retry_is_idempotent():
    source = 'I.loadSource(O.url),I.attachMedia(s)'
    once, count = _MOD.patch_hls_autoplay_after_attach(source)
    assert count == 1
    twice, count = _MOD.patch_hls_autoplay_after_attach(once)
    assert count == 0
    assert twice == once


def test_manifest_duration_is_not_skipped_by_existing_core_duration_patch():
    source = (
        'var A=this,u=null,O=!1,d=[],N=null,R=new i,c=!1,'
        'm={stream:!1,videoParams:!1},__stremioHlsResumeOffset=0;'
        'function L(e,t,a){"time"===t&&"number"==typeof a&&'
        '(__stremioHlsResumeOffset>0&&(a+=__stremioHlsResumeOffset)),'
        '"duration"===t&&"number"==typeof a&&'
        '(__stremioHlsResumeOffset>0&&(a+=__stremioHlsResumeOffset)),'
        'R.emit(e,t,p(t,a))}'
        'I.loadSource(O.url),I.attachMedia(s)'
    )
    source += (
        'case"duration":return null!==O&&null!==s.duration&&'
        'isFinite(s.duration)?Math.floor(1e3*s.duration):null;'
        '__stremioHlsResumeOffset=t.stream&&"string"==typeof t.stream.url&&'
        '-1!==t.stream.url.indexOf("/hlsv2/")&&"number"==typeof i.time&&'
        'isFinite(i.time)&&i.time>0?Math.round(i.time):0,'
        'N=null,__stremioHlsResumeOffset=0,D("stream")'
    )
    with_duration,n = _MOD.patch_hls_core_duration(source)
    assert n == 1
    assert 'window.__stremioHlsSourceDuration>0' in with_duration
    final,n = _MOD.patch_hls_autoplay_after_attach(with_duration)
    assert n == 1
    assert '/*stremio-hls-manifest-duration-v2*/' in final
    assert 'I.on(o.Events.MANIFEST_PARSED' in final
    assert 's.dispatchEvent(new Event("durationchange"))' in final
    assert 'searchParams.get("sourceDuration")' in final
    assert _MOD.patch_hls_autoplay_after_attach(final) == (final, 0)

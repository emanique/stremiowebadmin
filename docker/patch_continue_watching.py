#!/usr/bin/env python3
"""Patch bundled Stremio Web so Continue Watching centre Play resumes safely.

Upstream ContinueWatchingItem -> LibItem creates onPlayClick only when Core provides
deepLinks.player. The handler already carries the exact StreamBucket source and Core
resume position. Keep normal poster/details navigation untouched; mark only a click
inside Continue Watching's play overlay so MetaItem's parent click handler does not
also navigate to details/streams after React handles Play.
"""
from __future__ import annotations

import pathlib
import sys

BUILD = pathlib.Path("/srv/stremio-server/build")
MARKER = "stremio-webadmin: continue-watching-centre-play"

INJECTION = r"""
;/* stremio-webadmin: continue-watching-centre-play */
(()=>{if(window.__stremioWebadminContinueWatchingCentrePlay)return;
window.__stremioWebadminContinueWatchingCentrePlay=true;
document.addEventListener("click",e=>{
 const t=e.target instanceof Element?e.target:null;if(!t)return;
 const play=t.closest('[class*="play-icon-layer"]');if(!play)return;
 const poster=play.closest('[class*="poster-change-cursor"]');if(!poster)return;
 e.selectPrevented=true;
},true);})();
"""


def patch_text(text: str) -> tuple[str, int]:
    if MARKER in text:
        return text, 0
    return text + INJECTION, 1

# The bundled HTML5 player resets currentTime to zero during unload and only then publishes its
# time property. Preserve the last real time notification and provide a precise lifecycle event for
# the HLS cleanup loader before the media source is detached.
UNLOAD_EVENT = 'window.dispatchEvent(new Event("stremio-webadmin:player-unload"))'
UNLOAD_NEEDLE = 'removeAttribute("src"),'


def patch_player_unload(text: str) -> tuple[str, int]:
    if UNLOAD_EVENT in text:
        return text, 0
    # There are several player implementations in the bundle. Patch only unload sequences that
    # reset currentTime immediately after removeAttribute/load; hls.js internal detach sequences
    # do not have this shape and must remain untouched.
    import re
    pattern = re.compile(r'([A-Za-z_$][\w$]*)\.removeAttribute\("src"\),\1\.load\(\),\1\.currentTime=0,')
    def repl(m):
        v = m.group(1)
        return f'{UNLOAD_EVENT},{v}.removeAttribute("src"),{v}.load(),'
    patched, count = pattern.subn(repl, text)
    return patched, count




def patch_hls_resume_timeline(text: str) -> tuple[str, int]:
    """Keep Core time global while an HLS transcode starts at a server-side offset."""
    marker = '__stremioHlsResumeOffset=0;function L(e,t,a)'
    if marker in text:
        return text, 0

    state_old = 'var A=this,u=null,O=!1,d=[],N=null,R=new i,c=!1,m={stream:!1,videoParams:!1};function L(e,t,a){R.emit(e,t,p(t,a))}'
    state_new = (
        'var A=this,u=null,O=!1,d=[],N=null,R=new i,c=!1,m={stream:!1,videoParams:!1},'
        '__stremioHlsResumeOffset=0;function L(e,t,a){'
        '"time"===t&&"number"==typeof a&&(__stremioHlsResumeOffset>0&&(a+=__stremioHlsResumeOffset)),'
        '"duration"===t&&"number"==typeof a&&(__stremioHlsResumeOffset>0&&(a+=__stremioHlsResumeOffset)),'
        'R.emit(e,t,p(t,a))}'
    )
    if text.count(state_old) != 1:
        return text, 0
    text = text.replace(state_old, state_new)

    load_old = 'l.dispatch({type:"command",commandName:"load",commandArgs:Object.assign({},i,{stream:t.stream})})'
    load_new = (
        '__stremioHlsResumeOffset=t.stream&&"string"==typeof t.stream.url&&'
        '-1!==t.stream.url.indexOf("/hlsv2/")&&"number"==typeof i.time&&isFinite(i.time)&&i.time>0?Math.round(i.time):0,'
        'l.dispatch({type:"command",commandName:"load",commandArgs:Object.assign({},i,{stream:t.stream,'
        'time:__stremioHlsResumeOffset>0?0:i.time})})'
    )
    if text.count(load_old) != 1:
        return text, 0
    text = text.replace(load_old, load_new)

    unload_old = 'case"unload":return u=null,O=!1,d=[],N=null,D("stream"),D("videoParams"),!1;'
    unload_new = 'case"unload":return u=null,O=!1,d=[],N=null,__stremioHlsResumeOffset=0,D("stream"),D("videoParams"),!1;'
    if text.count(unload_old) != 1:
        return text, 0
    text = text.replace(unload_old, unload_new)
    return text, 1



def patch_external_subtitle_resume_query(text: str) -> tuple[str, int]:
    """Forward the HLS resume offset to external WebVTT proxy URLs."""
    marker = '["startTime",String(__stremioHlsResumeOffset)]'
    if marker in text:
        return text, 0
    needle = 'new URLSearchParams([["from",e.url]]).toString()'
    replacement = (
        'new URLSearchParams(__stremioHlsResumeOffset>0?'
        '[["from",e.url],["startTime",String(__stremioHlsResumeOffset)]]:'
        '[["from",e.url]]).toString()'
    )
    count = text.count(needle)
    if count != 2:
        return text, 0
    return text.replace(needle, replacement), count

def patch_hls_resume_query(text: str) -> tuple[str, int]:
    """Forward Stremio Core's millisecond resume offset to the HLS backend."""
    marker = 'r.set("startTime",Math.max(0,Math.round(i.time)))'
    if marker in text:
        return text, 0
    needle = 'var t=E(),r=new URLSearchParams([["mediaURL",a]]);return i.forceTranscoding&&r.set("forceTranscoding","1"),'
    replacement = (
        'var t=E(),r=new URLSearchParams([["mediaURL",a]]);'
        'return "number"==typeof i.time&&isFinite(i.time)&&i.time>0&&'
        'r.set("startTime",Math.max(0,Math.round(i.time))),'
        'i.forceTranscoding&&r.set("forceTranscoding","1"),'
    )
    count = text.count(needle)
    if count != 1:
        return text, 0
    return text.replace(needle, replacement), 1

def patch_hls_core_duration(text: str) -> tuple[str, int]:
    """Report the source duration to Core while an offset HLS EVENT playlist is still growing."""
    marker = "__stremioHlsSourceDuration"
    if marker in text:
        return text, 0

    state_old = 'm={stream:!1,videoParams:!1},__stremioHlsResumeOffset=0;function L(e,t,a){'
    state_new = 'm={stream:!1,videoParams:!1},__stremioHlsResumeOffset=0,__stremioHlsSourceDuration=0;function L(e,t,a){'
    if text.count(state_old) != 1:
        return text, 0
    text = text.replace(state_old, state_new)

    duration_old = '"duration"===t&&"number"==typeof a&&(__stremioHlsResumeOffset>0&&(a+=__stremioHlsResumeOffset)),'
    duration_new = '"duration"===t&&"number"==typeof a&&(window.__stremioHlsSourceDuration>0?a=window.__stremioHlsSourceDuration:__stremioHlsResumeOffset>0&&(a+=__stremioHlsResumeOffset)),'
    if text.count(duration_old) != 1:
        return text, 0
    text = text.replace(duration_old, duration_new)

    # hls.js EVENT playlists can expose an unknown/infinite HTMLMediaElement
    # duration during early metadata. Make the HTML5 property publish source
    # duration directly once the manifest hook has established it; otherwise
    # the Core bridge sees null and Continue Watching never gets corrected.
    browser_duration = (
        'case"duration":return null!==O&&null!==s.duration&&'
        'isFinite(s.duration)?Math.floor(1e3*s.duration):null;'
    )
    browser_duration_fixed = (
        'case"duration":return null!==O&&window.__stremioHlsSourceDuration>0&&'
        '"string"==typeof O.url&&-1!==O.url.indexOf("/hlsv2/")?'
        'window.__stremioHlsSourceDuration:'
        'null!==O&&null!==s.duration&&isFinite(s.duration)?'
        'Math.floor(1e3*s.duration):null;'
    )
    if text.count(browser_duration) != 1:
        return text, 0
    text = text.replace(browser_duration, browser_duration_fixed, 1)

    load_old = '__stremioHlsResumeOffset=t.stream&&"string"==typeof t.stream.url&&-1!==t.stream.url.indexOf("/hlsv2/")&&"number"==typeof i.time&&isFinite(i.time)&&i.time>0?Math.round(i.time):0,'
    load_new = (
        '__stremioHlsResumeOffset=t.stream&&"string"==typeof t.stream.url&&-1!==t.stream.url.indexOf("/hlsv2/")&&'
        '"number"==typeof i.time&&isFinite(i.time)&&i.time>0?Math.round(i.time):0,'
        'window.__stremioHlsSourceDuration=0,__stremioHlsSourceDuration=0,t.stream&&"string"==typeof t.stream.url&&'
        'function(){try{var e=new URL(t.stream.url,window.location.href),a=Number(e.searchParams.get("sourceDuration"));'
        'isFinite(a)&&a>0&&(window.__stremioHlsSourceDuration=a)}catch(e){}}(),'
    )
    if text.count(load_old) != 1:
        return text, 0
    text = text.replace(load_old, load_new)

    unload_old = 'N=null,__stremioHlsResumeOffset=0,D("stream")'
    unload_new = 'N=null,__stremioHlsResumeOffset=0,__stremioHlsSourceDuration=0,window.__stremioHlsSourceDuration=0,D("stream")'
    if text.count(unload_old) != 1:
        return text, 0
    return text.replace(unload_old, unload_new), 1


def patch_hls_autoplay_after_attach(text: str) -> tuple[str, int]:
    """Transfer sourceDuration from HLS variant URL into Core's duration.

    The server advertises sourceDuration on the variant, not on the master
    URL. This handler must run after MANIFEST_PARSED and emit durationchange,
    because hls.js's EVENT playlist duration is only the encoded-so-far
    window. A prior guard checked for a marker installed by the *other*
    duration patch, causing this essential hook to be skipped.
    """
    marker = '/*stremio-hls-manifest-duration-v2*/'
    if marker in text:
        return text, 0
    needle = 'I.loadSource(O.url),I.attachMedia(s)'
    replacement = (
        '/*stremio-hls-manifest-duration-v2*/'
        'I.on(o.Events.MANIFEST_PARSED,function(){'
        'try{var e=I.levels&&I.levels[0]&&I.levels[0].url,'
        't=Array.isArray(e)?e[0]:e;'
        'if("string"==typeof t){'
        'var a=Number(new URL(t,window.location.href).searchParams.get("sourceDuration"));'
        'if(isFinite(a)&&a>0){'
        'window.__stremioHlsSourceDuration=a;'
        's.dispatchEvent(new Event("durationchange"))'
        '}}}catch(e){}'
        'i.autoplay&&!s.paused||s.play().catch(function(){})'
        '}),I.loadSource(O.url),I.attachMedia(s)'
    )
    if text.count(needle) != 1:
        return text, 0
    return text.replace(needle, replacement), 1


def patch_initial_resume(text: str) -> tuple[str, int]:
    """Re-apply Core's initial resume time after media metadata is available."""
    import re
    marker = "__stremioResumeTime"
    if marker in text:
        return text, 0
    pattern = re.compile(
        r'([A-Za-z_$][\w$]*)\.autoplay="boolean"!=typeof ([A-Za-z_$][\w$]*)\.autoplay\|\|\2\.autoplay,'
        r'\1\.currentTime=null!==\2\.time&&isFinite\(\2\.time\)\?parseInt\(\2\.time,10\)/1e3:0,'
    )
    def repl(m):
        media, opts = m.group(1), m.group(2)
        return (
            f'{media}.autoplay="boolean"!=typeof {opts}.autoplay||{opts}.autoplay,'
            f'{media}.currentTime=null!=={opts}.time&&isFinite({opts}.time)?parseInt({opts}.time,10)/1e3:0,'
            f'{media}.__stremioResumeTime=null!=={opts}.time&&isFinite({opts}.time)?parseInt({opts}.time,10)/1e3:0,'
            f'{media}.addEventListener("canplay",function __stremioResume(){{var t={media}.__stremioResumeTime;'
            f'{media}.removeEventListener("canplay",__stremioResume);delete {media}.__stremioResumeTime;'
            f't>0&&{media}.currentTime<1&&({media}.currentTime=t)}},{{once:!0}}),'
        )
    return pattern.subn(repl, text)

def main() -> int:
    files = sorted(BUILD.rglob("scripts/main.js"))
    if len(files) != 1:
        print(f"[web-player] continue-watching patch skipped: main.js count={len(files)}")
        return 0

    path = files[0]
    text = path.read_text(encoding="utf-8")
    patched, status = patch_text(text)
    patched, unload_count = patch_player_unload(patched)
    patched, hls_resume_count = patch_hls_resume_query(patched)
    patched, hls_timeline_count = patch_hls_resume_timeline(patched)
    patched, external_sub_count = patch_external_subtitle_resume_query(patched)
    # Apply the Core-duration patch before autoplay: the autoplay patch also contains
    # the shared duration symbol, which must not trip core-duration's idempotency marker.
    patched, hls_core_duration_count = patch_hls_core_duration(patched)
    patched, hls_autoplay_count = patch_hls_autoplay_after_attach(patched)
    resume_count = 0
    if status == 1 or unload_count or hls_resume_count or hls_timeline_count or external_sub_count or hls_autoplay_count or hls_core_duration_count or resume_count:
        path.write_text(patched, encoding="utf-8")
        print(
            "[web-player] Continue Watching centre Play uses Core player "
            f"without parent stream navigation ({path})"
        )
    else:
        print("[web-player] continue-watching patch already applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())

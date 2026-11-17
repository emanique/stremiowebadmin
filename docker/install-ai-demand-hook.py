"""Patch the known, bundled WebPlayer subtitle-selection dispatch conservatively.

Fail the image build on upstream bundle changes rather than silently distributing
a broken integration or modifying unrelated player code.
"""
from __future__ import annotations

import pathlib
import sys

root = pathlib.Path(sys.argv[1])
files = list(root.glob("*/scripts/main.js"))
if len(files) != 1:
    raise SystemExit(f"expected one Stremio main.js; got {len(files)}")
path = files[0]
code = path.read_text("utf8")
needle = 'var i=v.find(function(e){return e.id===t});if(i||V(),i){'
if code.count(needle) != 1:
    raise SystemExit("unrecognised WebPlayer selected-extra-subtitles dispatch")
replacement = (
    'var i=v.find(function(e){return e.id===t});'
    'window.dispatchEvent(new CustomEvent("stremiosrv:subtitle-selection",'
    '{detail:{track:i||null}}));'
    'if(i||V(),i){'
)
code = code.replace(needle, replacement, 1)
# The subtitle variants menu can restore a previously selected track without
# invoking the HTML5 video backend's setProp hook. Observe the actual UI state.
# This also catches selection of embedded subtitles and OFF (deselects AI).
ui_needle = 'p=T.useCallback(function(t){t.embedded?"function"==typeof e.onSubtitlesTrackSelected'
if code.count(ui_needle) != 1:
    raise SystemExit("unrecognised WebPlayer subtitle variants selection callback")
ui_replacement = 'p=T.useCallback(function(t){window.dispatchEvent(new CustomEvent("stremiosrv:subtitle-selection",{detail:{track:t.embedded?null:t}}));t.embedded?"function"==typeof e.onSubtitlesTrackSelected'
code = code.replace(ui_needle, ui_replacement, 1)
# Language-level selection (including OFF) uses a separate click callback.
lang_needle = 'i=C(a,L).shift();i?i.embedded?'
if code.count(lang_needle) != 1:
    raise SystemExit("unrecognised WebPlayer subtitle language click handler")
lang_replacement = 'i=C(a,L).shift();window.dispatchEvent(new CustomEvent("stremiosrv:subtitle-selection",{detail:{track:i&&!i.embedded?i:null}}));i?i.embedded?'
code = code.replace(lang_needle, lang_replacement, 1)
# The browser has TWO subtitle renderers: Stremio's built-in "extra subtitles"
# renderer and our progressive AI overlay. For the Whisper addon only, match
# by the Stremio-provided `origin`, since its selected track ID is opaque. For AI tracks only, keep the track
# selected but give the built-in renderer a harmless, far-future SRT cue. The
# live text is displayed exclusively by our overlay, preventing duplicates.
# Do not disable the native renderer globally; ordinary subtitles are unchanged.
fetch_needle = 'var a=t?e.fallbackUrl:e.url;if("string"==typeof a)return fetch(a)'
if code.count(fetch_needle) != 1:
    raise SystemExit("unrecognised WebPlayer extra-subtitle fetch handler")
fetch_replacement = (
    'var a=t?e.fallbackUrl:e.url;'
    'if(e.origin==="AI Subtitle Generator"||/^ai-demand-[0-9a-f]{64}$/i.test(e.id||""))'
    'return Promise.resolve("1\\n99:59:58,000 --> 99:59:59,000\\n.\\n");'
    'if("string"==typeof a)return fetch(a)'
)
code = code.replace(fetch_needle, fetch_replacement, 1)
# Suppress the actual renderer, not just fetch: stale DOM captions from a
# previously loaded native SRT can otherwise remain visible after selection.
render_needle = 'function H(){var e=null!==D.time&&isFinite(D.time)?'
if code.count(render_needle) != 1:
    raise SystemExit("unrecognised WebPlayer native extra-subtitle renderer")
render_replacement = (
    'function H(){'
    'if(v.some(function(x){return x.id===g&&'
    '(x.origin==="AI Subtitle Generator"||/^ai-demand-[0-9a-f]{64}$/i.test(x.id||"")||'
    r'/\/ai-subtitles\//i.test(x.url||x.fallbackUrl||""))})){'
    'while(I.firstChild)I.removeChild(I.firstChild);'
    'p=null;return;}'
    'var e=null!==D.time&&isFinite(D.time)?'
)
code = code.replace(render_needle, render_replacement, 1)
# The HTML <video> clock can restart at zero on HLS/transcoding seeks.
# Stremio's native renderer stores D.time in MILLISECONDS (it adds
# Date.now() deltas). Convert to seconds before sending its playback clock
# to the AI overlay. Never use unconverted D.time in /demand endpoints.
clock_needle = 'case"time":D.time=a,D.lastSyncAt=Date.now();break;'
if code.count(clock_needle) != 1:
    raise SystemExit("unrecognised Stremio native playback clock callback")
clock_replacement = (
    'case"time":D.time=a,D.lastSyncAt=Date.now(),'
    'window.dispatchEvent(new CustomEvent("stremiosrv:playback-clock",'
    '{detail:{time:(a+(window.__stremioAiHlsOffsetMs||0))/1000,'
    'paused:D.paused,buffering:D.buffering}}));break;'
)
code = code.replace(clock_needle, clock_replacement, 1)
# HLS transcoding restarts HTML5 time at zero on a seek. The streaming
# adapter already knows the exact source offset in milliseconds, but keeps it
# inside its closure. Publish that offset for AI; leave the streaming adapter
# and normal subtitles unmodified. Direct play emits zero.
if '__stremioHlsResumeOffset=' in code:
    hls_start_needle = '__stremioHlsResumeOffset=t.stream&&"string"==typeof t.stream.url'
    if code.count(hls_start_needle) != 1:
        raise SystemExit("unrecognised HLS resume offset anchor")
    code = code.replace(hls_start_needle,
        'window.__stremioAiHlsOffsetMs=0,'
        '__stremioHlsResumeOffset=t.stream&&"string"==typeof t.stream.url', 1)
    hls_ready_needle = 'window.__stremioHlsSourceDuration=0,__stremioHlsSourceDuration=0,t.stream'
    if code.count(hls_ready_needle) != 1:
        raise SystemExit("unrecognised HLS offset set anchor")
    code = code.replace(hls_ready_needle,
        'window.__stremioAiHlsOffsetMs=__stremioHlsResumeOffset,'
        'window.__stremioHlsSourceDuration=0,__stremioHlsSourceDuration=0,t.stream', 1)
    hls_reset_needle = '__stremioHlsResumeOffset=0,__stremioHlsSourceDuration=0,window.__stremioHlsSourceDuration=0,D("stream")'
    if code.count(hls_reset_needle) != 1:
        raise SystemExit("unrecognised HLS unload reset anchor")
    code = code.replace(hls_reset_needle,
        '__stremioHlsResumeOffset=0,__stremioHlsSourceDuration=0,'
        'window.__stremioAiHlsOffsetMs=0,window.__stremioHlsSourceDuration=0,D("stream")', 1)
else:
    # Vanilla Stremio without the optional HLS resume adaptor.
    pass

# Mirror the actual HTML/MPV subtitle controls to the AI overlay.
# Native extra-subtitle renderer uses W / 25 * screen720p in vmin,
# I.style.bottom=U+"%", and extraSubtitlesDelay is in milliseconds.
# Use exact, fail-closed anchors so changes to upstream player code require QA.
subtitle_style_anchors = [
    ('case"extraSubtitlesSize":return null!==t&&isFinite(t)&&(W=Math.max(0,parseInt(t,10)),',
     'case"extraSubtitlesSize":return null!==t&&isFinite(t)&&(W=Math.max(0,parseInt(t,10)),'
     'window.dispatchEvent(new CustomEvent("stremiosrv:ai-subtitle-settings",'
     '{detail:{sizePercent:W}})),'),
    ('case"extraSubtitlesOffset":return null!==t&&isFinite(t)&&(U=Math.max(0,Math.min(100,parseInt(t,10))),',
     'case"extraSubtitlesOffset":return null!==t&&isFinite(t)&&(U=Math.max(0,Math.min(100,parseInt(t,10))),'
     'window.dispatchEvent(new CustomEvent("stremiosrv:ai-subtitle-settings",'
     '{detail:{verticalPercent:U}})),'),
    ('case"extraSubtitlesDelay":return null!==g&&null!==t&&isFinite(t)&&(M=parseInt(t,10),',
     'case"extraSubtitlesDelay":return null!==g&&null!==t&&isFinite(t)&&(M=parseInt(t,10),'
     'window.dispatchEvent(new CustomEvent("stremiosrv:ai-subtitle-settings",'
     '{detail:{delaySeconds:M/1000}})),'),
]
for original, updated in subtitle_style_anchors:
    if code.count(original) != 1:
        raise SystemExit("unrecognised native subtitle settings setter")
    code = code.replace(original, updated, 1)

# Copy the renderer's *effective* colours (already normalised by its
# colour parser) and opacity to the AI overlay. This is presentation-only.
subtitle_colour_anchors = [
    ('P=!0,H(),w("extraSubtitlesTextColor")',
     'window.dispatchEvent(new CustomEvent("stremiosrv:ai-subtitle-settings",'
     '{detail:{textColor:G}})),P=!0,H(),w("extraSubtitlesTextColor")'),
    ('P=!0,H(),w("extraSubtitlesBackgroundColor")',
     'window.dispatchEvent(new CustomEvent("stremiosrv:ai-subtitle-settings",'
     '{detail:{backgroundColor:y}})),P=!0,H(),w("extraSubtitlesBackgroundColor")'),
    ('P=!0,H(),w("extraSubtitlesOutlineColor")',
     'window.dispatchEvent(new CustomEvent("stremiosrv:ai-subtitle-settings",'
     '{detail:{outlineColor:b}})),P=!0,H(),w("extraSubtitlesOutlineColor")'),
    ('P=!0,H(),w("extraSubtitlesOpacity")',
     'window.dispatchEvent(new CustomEvent("stremiosrv:ai-subtitle-settings",'
     '{detail:{opacityPercent:Math.round(k*100)}})),P=!0,H(),w("extraSubtitlesOpacity")'),
]
for original, updated in subtitle_colour_anchors:
    if code.count(original) != 1:
        raise SystemExit("unrecognised native subtitle colour/opacity setter")
    code = code.replace(original, updated, 1)

# Group the addon under its own pseudo-language in the *existing* Stremio
# subtitles menu. Do not alter language codes from the addon manifest or
# modify third-party/embedded subtitle tracks elsewhere in the player.
ai_lang = "__local_server_ai_translator__"
# Stremio delivers addon-provided subtitles through subtitlesTracks, not
# necessarily extraSubtitlesTracks. Normalise BOTH before building languages.
normal_needle = 'a=T.useMemo(function(){return D(Array.isArray(e.subtitlesTracks)?e.subtitlesTracks:[])},[e.subtitlesTracks])'
if code.count(normal_needle) != 1:
    raise SystemExit("unrecognised WebPlayer primary subtitle language grouping")
# Stremio assigns opaque IDs. Give each addon an independent language group;
# retain native track IDs/URLs/labels, so each addon runs its own logic.
# Never hide either addon or merge their tracks.
def ai_normalise_source(var):
    return (
        '(function(t){var u=t.url||t.fallbackUrl||"";'
        'return /^ai-demand-[0-9a-f]{64}$/i.test(t.id||"")||'
        '(t.origin==="AI Subtitle Generator")||'
        r'/\/ai-subtitles\/[^\/]+\/(?:placeholder|file)\//i.test(u)?'
        'Object.assign({},t,{lang:"__local_server_ai_translator__",'
        'label:"AI to PT",'
        'name:"AI to PT"}):'
        '(t.origin==="Stremio Server Addon · Tradutor"||'
        't.origin==="Subtitle Translator"||'
        r'/\/subtitle-translator\/[^\/]+\/(?:select|file)\//i.test(u))?'
        'Object.assign({},t,{lang:"__local_subtitle_translator__",'
        'label:(t.name&&!/^__local_subtitle_/i.test(t.name)?t.name:(t.label&&!/^__local_subtitle_/i.test(t.label)?t.label:("Tradução pt-PT · "+String(t.addonSubtitleId||t.id||"fonte").slice(0,12))))}):t})'
    )
normal_replacement = (
    'a=T.useMemo(function(){return D(Array.isArray(e.subtitlesTracks)?'
    'e.subtitlesTracks:[]).map(function(t){return '+ ai_normalise_source('a') +
    '(t)})},[e.subtitlesTracks])'
)
code = code.replace(normal_needle, normal_replacement, 1)

tracks_needle = 'i=T.useMemo(function(){return D(Array.isArray(e.extraSubtitlesTracks)?e.extraSubtitlesTracks:[])},[e.extraSubtitlesTracks])'
if code.count(tracks_needle) != 1:
    raise SystemExit("unrecognised WebPlayer extra subtitle language grouping")
tracks_replacement = (
    'i=T.useMemo(function(){'
    'var __aiTracks=D(Array.isArray(e.extraSubtitlesTracks)?'
    'e.extraSubtitlesTracks:[]).map(function(t){return ' + ai_normalise_source('i') + '(t)});'
    'return __aiTracks},[a,e.extraSubtitlesTracks])'
)
code = code.replace(tracks_needle, tracks_replacement, 1)
# A language is a navigation choice, not a subtitle track. The former synthetic
# "source picker" track had no ID in Stremio's canonical tracks and therefore
# could not reliably activate the variants column. Retain a local UI language
# choice until a real variant is clicked; never start a server job on language click.
state_needle = 'var a=T.useMemo(function(){return D(Array.isArray(e.subtitlesTracks)'
if code.count(state_needle) != 1:
    raise SystemExit("unrecognised WebPlayer menu hook state anchor")
code = code.replace(state_needle,
    'var __aiChoice=T.useState(null),__aiLang=__aiChoice[0],'
    '__setAiLang=__aiChoice[1],a=T.useMemo(function(){return D(Array.isArray(e.subtitlesTracks)',
    1)
selected_needle = '_=T.useMemo(function(){return"string"==typeof e.selectedSubtitlesTrackId?'
if code.count(selected_needle) != 1:
    raise SystemExit("unrecognised WebPlayer selected language useMemo")
code = code.replace(selected_needle,
    '_=T.useMemo(function(){if(__aiLang)return __aiLang;return"string"==typeof e.selectedSubtitlesTrackId?',
    1)
selected_dep_needle = '},[a,i,e.selectedSubtitlesTrackId,e.selectedExtraSubtitlesTrackId])'
if code.count(selected_dep_needle) != 1:
    raise SystemExit("unrecognised WebPlayer selected language dependency list")
code = code.replace(selected_dep_needle,
    '},[a,i,e.selectedSubtitlesTrackId,e.selectedExtraSubtitlesTrackId,__aiLang])', 1)
ai_click_needle = 'I=T.useCallback(function(t){var a=E.filter(function(e){return e.lang===t.currentTarget.dataset.lang})'
if code.count(ai_click_needle) != 1:
    raise SystemExit("unrecognised WebPlayer language click callback")
code = code.replace(ai_click_needle,
    'I=T.useCallback(function(t){var __lang=t.currentTarget.dataset.lang;'
    'if(__lang==="__local_server_ai_translator__"||__lang==="__local_subtitle_translator__"){__setAiLang(__lang);return;}'
    '__setAiLang(null);'
    'var a=E.filter(function(e){return e.lang===t.currentTarget.dataset.lang})', 1)
# A real variant resets navigation choice; Stremio's selected track id now
# resolves to the normalized language in both source arrays.
real_variant_needle = 'p=T.useCallback(function(t){window.dispatchEvent(new CustomEvent("stremiosrv:subtitle-selection"'
if code.count(real_variant_needle) != 1:
    raise SystemExit("unrecognised WebPlayer real variant click callback")
code = code.replace(real_variant_needle,
    'p=T.useCallback(function(t){__setAiLang(null);'
    'window.dispatchEvent(new CustomEvent("stremiosrv:subtitle-selection"', 1)

label_needle = '"local"===e?R("LOCAL"):l.label(e)'
if code.count(label_needle) != 1:
    raise SystemExit("unrecognised WebPlayer subtitle language label")
code = code.replace(label_needle,
    '"__local_server_ai_translator__"===e?"Local Server AI Translator":'
    '"__local_subtitle_translator__"===e?"Subtitle Translator":'
    '"local"===e?R("LOCAL"):l.label(e)', 1)
title_needle = 'title:l.label(e),className:s(N["language-option"]'
if code.count(title_needle) != 1:
    raise SystemExit("unrecognised WebPlayer subtitle language tooltip")
code = code.replace(title_needle,
    'title:"__local_server_ai_translator__"===e?'
    '"Local Server AI Translator":'
    '"__local_subtitle_translator__"===e?"Subtitle Translator":'
    'l.label(e),className:s(N["language-option"]', 1)
# Stremio's *variant* component can derive its displayed label from the
# pseudo-language even after the menu normalisation. Guard at the final render
# boundary, where the end-user string is produced; preserve id, URLs and events.
variant_needle = 'd=a.fallbackUrl||a.url,N=u(a.label)?a.label:s.languages.label(a.lang)'
if code.count(variant_needle) != 1:
    raise SystemExit("unrecognised subtitle variant label renderer")
variant_replacement = (
    'd=a.fallbackUrl||a.url,'
    'N=a.lang==="__local_subtitle_translator__"?'
    '(function(){var __m=null;'
    '[a.fallbackUrl,a.url].forEach(function(__candidate){'
    'if(__m||typeof __candidate!=="string")return;'
    'for(var __n=0;__n<4;__n++){'
    'var __match=__candidate.match(/[?&]stremioLabel=([^&#]+)/i);'
    'if(__match){try{__m=decodeURIComponent(__match[1].replace(/\\+/g,"%20"));}catch(__err){}break;}'
    'if(!/%[0-9a-f]{2}/i.test(__candidate))break;'
    'try{__candidate=decodeURIComponent(__candidate);}catch(__err){break;}'
    '}});return __m||'
    '(u(a.label)&&!/^__local_subtitle_/i.test(a.label)?a.label:'
    '(u(a.name)&&!/^__local_subtitle_/i.test(a.name)?a.name:'
    '("Tradução pt-PT · "+String(a.addonSubtitleId||a.id||"fonte").slice(-8))));})():'

    '(u(a.label)?a.label:s.languages.label(a.lang))'
)
code = code.replace(variant_needle, variant_replacement, 1)

# No synthetic subtitle track is added; language click navigates locally.
# Only a real variant selection triggers the native handler.
path.write_text(code, encoding="utf8")
print("WebPlayer subtitle selection hook installed; exact dispatch verified")

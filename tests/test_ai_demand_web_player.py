"""The WebPlayer addon-selection hook cannot work by prefetch alone."""
import ast
import pathlib
import subprocess
import shutil
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="requires Node.js")
def test_real_selection_starts_and_deselection_stops():
    out = subprocess.run(
        ["node", str(ROOT/"tests/fixtures/ai_demand_player_harness.js"),
         str(ROOT/"docker/ai-demand-web-player.js")],
        capture_output=True, text=True, timeout=20, check=True)
    assert '"starts":6' in out.stdout
    assert '"stops":6' in out.stdout


def test_installer_is_fail_closed_on_new_webplayer_bundle():
    code = (ROOT/"docker/install-ai-demand-hook.py").read_text("utf-8")
    assert "code.count(needle) != 1" in code
    assert "stremiosrv:subtitle-selection" in code
    installer = (ROOT/"docker/install-web-player-loader.sh").read_text("utf-8")
    assert "STREMIOSRV_AI_PLAYER_PATCH" in installer
    assert "MAIN_REV=$(md5sum" in installer


def test_patch_adds_variants_menu_and_language_hooks():
    source = (ROOT / "docker/install-ai-demand-hook.py").read_text("utf8")
    assert "ui_needle" in source and "lang_needle" in source
    assert "unrecognised WebPlayer subtitle variants" in source
    assert "subtitle-selection" in source


def test_native_extra_subtitle_renderer_is_suppressed_only_for_ai_tracks():
    """Avoid two on-screen copies while leaving other addons untouched."""
    installer = (ROOT / "docker/install-ai-demand-hook.py").read_text("utf8")
    assert 'fetch_needle' in installer
    assert 'code.count(fetch_needle) != 1' in installer
    assert 'ai-demand-[0-9a-f]{64}' in installer
    assert '99:59:58,000 --> 99:59:59,000' in installer
    assert 'if("string"==typeof a)return fetch(a)' in installer


def test_ai_subtitles_have_dedicated_language_group():
    installer = (ROOT / "docker/install-ai-demand-hook.py").read_text("utf8")
    assert "Local Server AI Translator" in installer
    assert "__local_server_ai_translator__" in installer
    assert "tracks_needle" in installer and "code.count(tracks_needle) != 1" in installer
    assert "title_needle" in installer and "label_needle" in installer
    # Third-party and embedded subtitle language mapping must remain unchanged.
    assert 'label:"AI to PT"' in installer

def test_independent_subtitle_translator_track_remains_visible():
    """AI to PT and Subtitle Translator are independent; neither filters the other."""
    patch = (ROOT / "docker/install-ai-demand-hook.py").read_text("utf8")
    assert 't.origin!=="Stremio Server Addon · Tradutor"' not in patch
    assert "normal_replacement" in patch and "tracks_replacement" in patch
    assert 't.origin!=="Stremio Server Addon · Tradutor"' not in patch
    assert "AI to PT" in patch


def test_ai_patch_installer_is_valid_python_and_has_no_device_footer():
    patch = (ROOT / "docker/install-ai-demand-hook.py").read_text("utf8")
    ast.parse(patch)
    assert "[executed on device:" not in patch


def test_ai_language_click_uses_local_navigation_without_selecting_a_subtitle():
    patch = (ROOT / "docker/install-ai-demand-hook.py").read_text("utf8")
    assert "__aiChoice=T.useState(null)" in patch
    assert '__lang==="__local_server_ai_translator__"||__lang==="__local_subtitle_translator__"' in patch
    assert "__setAiLang(__lang);return;" in patch
    assert "if(__aiLang)return __aiLang" in patch
    assert "__setAiLang(null)" in patch
    assert 'id:"stremiosrv-ai-choose-source"' not in patch


def test_ai_source_group_created_only_from_real_subtitles():
    installer = (ROOT / "docker/install-ai-demand-hook.py").read_text("utf8")
    assert "normal_replacement" in installer and "tracks_replacement" in installer
    assert 't.origin!=="Stremio Server Addon · Tradutor"' not in installer
    assert "normal_needle" in installer
    assert "tracks_needle" in installer
    assert '__aiTracks.unshift(' not in installer


def test_webplayer_patch_applies_to_expected_bundle_signatures(tmp_path):
    """A synthetic fixture enforces exact upstream signatures, no loose string patches."""
    build = tmp_path / "web"
    main = build / ("a" * 40) / "scripts" / "main.js"
    main.parent.mkdir(parents=True)
    patterns = [
        'var i=v.find(function(e){return e.id===t});if(i||V(),i){',
        'p=T.useCallback(function(t){t.embedded?"function"==typeof e.onSubtitlesTrackSelected',
        'i=C(a,L).shift();i?i.embedded?',
        'var a=t?e.fallbackUrl:e.url;if("string"==typeof a)return fetch(a)',
        'function H(){var e=null!==D.time&&isFinite(D.time)?',
        'case"time":D.time=a,D.lastSyncAt=Date.now();break;',
        'var a=T.useMemo(function(){return D(Array.isArray(e.subtitlesTracks)?e.subtitlesTracks:[])},[e.subtitlesTracks])',
        'i=T.useMemo(function(){return D(Array.isArray(e.extraSubtitlesTracks)?e.extraSubtitlesTracks:[])},[e.extraSubtitlesTracks])',
        '_=T.useMemo(function(){return"string"==typeof e.selectedSubtitlesTrackId?',
        '},[a,i,e.selectedSubtitlesTrackId,e.selectedExtraSubtitlesTrackId])',
        'I=T.useCallback(function(t){var a=E.filter(function(e){return e.lang===t.currentTarget.dataset.lang})',
        '"local"===e?R("LOCAL"):l.label(e)',
        'title:l.label(e),className:s(N["language-option"]',
    ]
    main.write_text("\\n".join(patterns), encoding="utf8")
    proc = subprocess.run(
        ["python3", str(ROOT / "docker/install-ai-demand-hook.py"), str(build)],
        capture_output=True, text=True, check=True,
    )
    result = main.read_text("utf8")
    assert proc.returncode == 0
    assert '__aiChoice=T.useState(null)' in result
    assert '__setAiLang(__lang);return;' in result
    assert 'label:"AI to PT"' in result
    assert 'window.dispatchEvent(new CustomEvent("stremiosrv:subtitle-selection"' in result


def test_neutral_ai_picker_is_suppressed_in_both_native_renderer_paths():
    """No placeholder network fetch and no stale native captions on source choice."""
    patch = (ROOT / "docker/install-ai-demand-hook.py").read_text("utf8")
    assert 'if(e.origin==="AI Subtitle Generator"||' in patch
    assert 'x.origin==="AI Subtitle Generator"||' in patch
    assert 'Promise.resolve("1\\\\n99:59:58,000 --> 99:59:59,000' in patch
    assert 'while(I.firstChild)I.removeChild(I.firstChild)' in patch


def test_ai_pseudo_language_is_not_required_to_be_known_by_stremio_registry():
    """The bundle uses the returned code verbatim for unknown language identifiers."""
    patch = (ROOT / "docker/install-ai-demand-hook.py").read_text("utf8")
    assert 'lang:"__local_server_ai_translator__"' in patch
    assert 'title:"__local_server_ai_translator__"===e?' in patch


def test_progressive_ai_overlay_uses_native_subtitle_settings():
    script = (ROOT / "docker/ai-demand-web-player.js").read_text("utf8")
    assert "observeNativeSettings" in script
    assert "stremiosrv:ai-subtitle-settings" in script
    assert "movieTime - styleSettings.delaySeconds" in script
    assert "styleSettings.sizePercent / 100" in script
    assert "styleSettings.verticalPercent / 100" in script
    assert "document.addEventListener('seeking'" in script
    assert "cues = [];" in script


def test_progressive_ai_settings_are_bounded_and_do_not_write_native_controls():
    script = (ROOT / "docker/ai-demand-web-player.js").read_text("utf8")
    assert "Math.max(-600, Math.min(600, patch.delaySeconds))" in script
    assert "Math.max(50, Math.min(250, patch.sizePercent))" in script
    assert "Math.max(0, Math.min(100, patch.verticalPercent))" in script
    assert "node.querySelector" in script
    assert "node.click(" not in script


@pytest.mark.skipif(shutil.which("node") is None, reason="requires Node.js")
def test_seek_discards_late_srt_and_off_stops_session():
    out = subprocess.run(
        ["node", str(ROOT / "tests/fixtures/ai_subtitle_stale_srt_harness.js"),
         str(ROOT / "docker/ai-demand-web-player.js")],
        capture_output=True, text=True, timeout=20, check=True,
    )
    assert '"staleRejected":true' in out.stdout
    assert '"starts":1' in out.stdout
    assert '"stops":1' in out.stdout


def test_inflight_subtitles_are_invalidated_on_session_or_seek_change():
    script = (ROOT / "docker/ai-demand-web-player.js").read_text("utf8")
    assert "var playbackEpoch = 0" in script
    assert "playbackEpoch += 1" in script
    assert "epoch !== playbackEpoch" in script
    assert "sessionId !== mine.sessionId" in script


def test_webplayer_aborts_obsolete_poll_and_bounds_network_wait():
    script = (ROOT / "docker/ai-demand-web-player.js").read_text("utf8")
    assert "var pendingPollController = null;" in script
    assert "if (pendingPollController) pendingPollController.abort();" in script
    assert "new AbortController()" in script
    assert "controller.signal" in script
    assert "controller.abort(); }, 12000)" in script
    assert "err.name !== 'AbortError'" in script


@pytest.mark.skipif(shutil.which("node") is None, reason="requires Node.js")
def test_pause_during_pending_start_cancels_stale_session_and_resume_restarts():
    out = subprocess.run(
        ["node", str(ROOT / "tests/fixtures/ai_subtitle_paused_start_harness.js"),
         str(ROOT / "docker/ai-demand-web-player.js")],
        capture_output=True, text=True, timeout=20, check=True,
    )
    assert '"pauseAbort":true' in out.stdout
    assert '"staleStartStopped":true' in out.stdout
    assert '"resumeStarts":2' in out.stdout
    assert '"offStops":true' in out.stdout


def test_start_response_is_guarded_by_selection_generation():
    source = (ROOT / "docker/ai-demand-web-player.js").read_text("utf8")
    assert "active !== mine || stamp !== generation" in source
    assert "if (active === mine && stamp === generation)" in source
    assert "if (pendingStartController) pendingStartController.abort();" in source
    assert "active.pausedForUser = true;" in source


def test_addon_tracks_are_grouped_from_both_native_arrays():
    """Stremio may place addon tracks in subtitlesTracks or extraSubtitlesTracks."""
    source = (ROOT / "docker/install-ai-demand-hook.py").read_text("utf8")
    assert "normal_needle" in source
    assert "code.count(normal_needle) != 1" in source
    assert "e.subtitlesTracks" in source
    assert "e.extraSubtitlesTracks" in source
    assert "normal_replacement" in source and "tracks_replacement" in source
    assert "ai-subtitles" in source
    assert "placeholder|file" in source


def test_origin_based_native_suppression_is_limited_to_whisper():
    patch = (ROOT / "docker/install-ai-demand-hook.py").read_text("utf8")
    assert 'if(e.origin==="AI Subtitle Generator"||' in patch
    assert '(x.origin==="AI Subtitle Generator"||' in patch
    fetch_section = patch.split('fetch_replacement = (')[1].split('code = code.replace(fetch_needle')[0]
    assert 'Stremio Server Addon · Tradutor' not in fetch_section
    assert 't.origin!=="Stremio Server Addon · Tradutor"' not in patch


def test_ai_menu_shows_only_named_ai_to_pt_variant():
    source = (ROOT / 'docker/install-ai-demand-hook.py').read_text('utf8')
    assert 'label:"AI to PT"' in source
    assert 'name:"AI to PT"' in source
    assert source.count('filter(function(t){return t.origin!=="Stremio Server Addon · Tradutor"})') == 0


@pytest.mark.skipif(shutil.which("node") is None, reason="requires Node.js")
def test_absolute_stremio_clock_not_relative_media_clock():
    out = subprocess.run(
        ["node", str(ROOT / "tests/fixtures/ai_subtitle_absolute_clock_harness.js"),
         str(ROOT / "docker/ai-demand-web-player.js")],
        capture_output=True, text=True, timeout=20, check=True,
    )
    assert '"trustedStart":true' in out.stdout
    assert '"trustedHeartbeat":true' in out.stdout
    assert '"absoluteCueRendered":true' in out.stdout
    assert '"relativeMediaTime":70' in out.stdout
    assert '"absoluteMovieTime":1200' in out.stdout


def test_absolute_player_clock_hook_and_startup_pause_clock_validity():
    patch = (ROOT / "docker/install-ai-demand-hook.py").read_text("utf8")
    overlay = (ROOT / "docker/ai-demand-web-player.js").read_text("utf8")
    assert 'case"time":D.time=a,D.lastSyncAt=Date.now();break;' in patch
    assert 'stremiosrv:playback-clock' in patch
    assert '(a+(window.__stremioAiHlsOffsetMs||0))/1000' in patch
    assert 'stremiosrv:playback-clock' in overlay
    assert 'positionSeconds: position' in overlay
    assert 'pollStatus(active, movieTime)' in overlay
    assert 'item.start <= movieTime - styleSettings.delaySeconds' in overlay
    assert 'if (age < 0 || (age > 12 && !(media && media.paused)' in overlay


def test_clock_units_avoid_future_subtitle_windows():
    patch = (ROOT / "docker/install-ai-demand-hook.py").read_text("utf8")
    assert "(a+(window.__stremioAiHlsOffsetMs||0))/1000" in patch
    # A native 72,430-millisecond sample is 72.43 seconds, not 20 hours.
    assert 72430 / 1000 == 72.43


def test_subtitle_translator_uses_own_menu_without_changing_functionality():
    patch = (ROOT / "docker/install-ai-demand-hook.py").read_text("utf8")
    assert 't.origin==="AI Subtitle Generator"' in patch
    assert 't.origin==="Stremio Server Addon · Tradutor"' in patch
    assert 'lang:"__local_server_ai_translator__"' in patch
    assert 'lang:"__local_subtitle_translator__"' in patch
    assert 'label:"AI to PT"' in patch
    assert '"Subtitle Translator"' in patch
    assert '__lang==="__local_server_ai_translator__"||__lang==="__local_subtitle_translator__"' in patch
    assert 't.origin!=="Stremio Server Addon · Tradutor"' not in patch
    # Only presentation metadata changes; no mutation of content URLs or IDs.
    assert 'Object.assign({},t,{lang:"__local_subtitle_translator__",' in patch
    assert 't.name&&!/^__local_subtitle_/i.test(t.name)?t.name' in patch
    assert '!/^__local_subtitle_/i.test(t.label)' in patch



def test_hls_resume_offset_combines_milliseconds_without_changing_direct_play():
    patch = (ROOT / "docker/install-ai-demand-hook.py").read_text("utf8")
    assert "hls_start_needle" in patch and "hls_ready_needle" in patch
    assert "hls_reset_needle" in patch
    assert "window.__stremioAiHlsOffsetMs=__stremioHlsResumeOffset" in patch
    assert "(a+(window.__stremioAiHlsOffsetMs||0))/1000" in patch
    assert (183000 + 2252420)/1000 == 2435.42
    assert (183000 + 0)/1000 == 183


def test_translator_variant_renderer_never_displays_pseudo_language():
    patch = (ROOT / "docker/install-ai-demand-hook.py").read_text("utf8")
    assert "variant_needle = 'd=a.fallbackUrl||a.url,N=u(a.label)?a.label:s.languages.label(a.lang)'" in patch
    assert 'N=a.lang==="__local_subtitle_translator__"?' in patch
    assert 'Tradução pt-PT · ' in patch
    assert '!/^__local_subtitle_/i.test(a.label)' in patch


def test_translator_source_is_carried_only_as_display_url_parameter():
    source = (ROOT / "src/stremiosrv/subtitle_translator/addon.py").read_text("utf8")
    patch = (ROOT / "docker/install-ai-demand-hook.py").read_text("utf8")
    assert source.count('stremioLabel=') == 2
    assert 'quote(display, safe="")' in source
    assert 'decodeURIComponent' in patch
    assert '[a.fallbackUrl,a.url].forEach' in patch
    assert 'stremioLabel=([^&#]+)' in patch


def test_translator_variant_prefers_original_fallback_url_and_unwraps_proxy():
    patch = (ROOT / "docker/install-ai-demand-hook.py").read_text("utf8")
    assert '[a.fallbackUrl,a.url].forEach' in patch
    assert 'decodeURIComponent(__candidate)' in patch
    assert 'stremioLabel=([^&#]+)' in patch
    assert 'for(var __n=0;__n<4;__n++)' in patch
    assert 'QA u=' not in patch


def test_native_mpv_shell_can_run_ai_without_html_video():
    """The Stremio native shell emits a player clock, not an HTML video."""
    node = shutil.which("node")
    if not node:
        pytest.skip("requires Node.js")
    proc = subprocess.run([
        node, str(ROOT / "tests/fixtures/ai_demand_native_shell_harness.js"),
        str(ROOT / "docker/ai-demand-web-player.js")],
        capture_output=True, text=True, timeout=20, check=True)
    assert '"starts":2' in proc.stdout
    assert '"stops":2' in proc.stdout
    assert '"nativeOverlay":true' in proc.stdout


def test_ai_style_uses_native_size_and_position_events():
    code = (ROOT / "docker/ai-demand-web-player.js").read_text("utf8")
    installer = (ROOT / "docker/install-ai-demand-hook.py").read_text("utf8")
    assert "styleSettings.sizePercent / 25 * nativeScale" in code
    assert "bounds.height * styleSettings.verticalPercent / 100" in code
    assert "element.style.top = 'auto'" in code
    assert 'case"extraSubtitlesSize"' in installer
    assert 'case"extraSubtitlesOffset"' in installer
    assert 'case"extraSubtitlesDelay"' in installer
    assert 'stremiosrv:ai-subtitle-settings' in installer


def test_ai_overlay_inherits_native_colours_background_outline_opacity():
    patch = (ROOT / "docker/install-ai-demand-hook.py").read_text("utf8")
    overlay = (ROOT / "docker/ai-demand-web-player.js").read_text("utf8")
    for prop in ("extraSubtitlesTextColor", "extraSubtitlesBackgroundColor",
                 "extraSubtitlesOutlineColor", "extraSubtitlesOpacity"):
        assert prop in patch
    for value in ("textColor:G", "backgroundColor:y", "outlineColor:b",
                  "opacityPercent:Math.round(k*100)"):
        assert value in patch
    assert "cue.style.backgroundColor = styleSettings.backgroundColor" in overlay
    assert "cue.style.color = styleSettings.textColor" in overlay
    assert "cue.style.textShadow" in overlay
    assert "cue.style.opacity" in overlay
    assert "element.appendChild(cue)" in overlay


def test_ai_font_size_is_on_native_subtitle_cue_not_overlay_container():
    source = (ROOT / "docker/ai-demand-web-player.js").read_text("utf8")
    assert "cue.style.fontSize = cueFontSize" in source
    assert "cue.style.display = 'inline-block'" in source
    assert "cue.style.whiteSpace = 'pre-wrap'" in source
    assert "element.style.fontSize = Math.floor" not in source


def test_partial_torrent_native_clock_waits_for_resume():
    code = (ROOT / "docker/ai-demand-web-player.js").read_text("utf8")
    assert "var nativeClockResuming = false" in code
    assert "var wasBuffering = !!(old && old.buffering)" in code
    assert "time > nativeClockResumeAt + 0.15" in code
    assert "playerClock.buffering || nativeClockResuming" in code

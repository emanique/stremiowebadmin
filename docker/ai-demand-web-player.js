// AI subtitle overlay for the WebPlayer and Stremio native shell.
// Only explicit selection and a fresh Stremio playback-clock event start demand.
// Never infer playback position from HTTP requests, torrent bytes or wall clock.
(function () {
    'use strict';
    if (typeof document === 'undefined' || !document.querySelector) return;

    var active = null;
    var view = null;
    var lastHeartbeat = 0;
    var lastSubtitlePoll = 0;
    var cues = [];
    var requestBusy = false;
    var pendingPollController = null;
    var pendingStartController = null;
    var generation = 0;
    // Invalidates network snapshots fetched before a seek or a new selection.
    var playbackEpoch = 0;
    var playerClock = null;
    // Native MPV may report a stale timeline while a partially downloaded
    // torrent buffers. Never display cues until its clock advances again.
    var nativeClockResuming = false;
    var nativeClockResumeAt = null;
    // The regular Stremio controls are rendered in a menu; retain the last
    // observed settings after the menu closes. These affect only the AI overlay.
    var styleSettings = {
        delaySeconds: 0, sizePercent: 100, verticalPercent: 0,
        textColor: 'rgb(255, 255, 255)',
        backgroundColor: 'rgba(0, 0, 0, 0)',
        outlineColor: 'rgb(34, 34, 34)',
        opacityPercent: 100
    };

    function updateSettings(patch) {
        if (!patch || typeof patch !== 'object') return;
        if (Number.isFinite(patch.delaySeconds))
            styleSettings.delaySeconds = Math.max(-600, Math.min(600, patch.delaySeconds));
        if (Number.isFinite(patch.sizePercent))
            styleSettings.sizePercent = Math.max(50, Math.min(250, patch.sizePercent));
        if (Number.isFinite(patch.verticalPercent))
            styleSettings.verticalPercent = Math.max(0, Math.min(100, patch.verticalPercent));
        // Copy the Stremio subtitle renderer's effective colours. Only accept
        // CSS colour values; never embed text into style attributes or HTML.
        ['textColor', 'backgroundColor', 'outlineColor'].forEach(function (key) {
            var value = patch[key];
            if (typeof value !== 'string' || value.length > 100) return;
            if (/^(?:rgba?\([\d\s.,%]+\)|#[0-9a-f]{3,8})$/i.test(value))
                styleSettings[key] = value;
        });
        if (Number.isFinite(patch.opacityPercent))
            styleSettings.opacityPercent = Math.max(0, Math.min(100, patch.opacityPercent));
    }

    // Derive settings from the existing Stremio stepper labels/values while
    // the subtitles menu is visible. Never click, mutate or replace controls.
    function observeNativeSettings() {
        if (!active || typeof document.querySelectorAll !== 'function') return;
        var menu = document.querySelector('[class*="subtitles-menu-container"]');
        if (!menu || typeof menu.querySelectorAll !== 'function') return;
        var nodes = menu.querySelectorAll('[class*="stepper"]');
        Array.prototype.forEach.call(nodes, function (node) {
            var header = node.querySelector('[class*="header"]');
            var value = node.querySelector('[class*="value"]');
            if (!header || !value) return;
            var name = String(header.textContent || '').trim().toUpperCase();
            var raw = String(value.textContent || '').trim();
            var number = Number.parseFloat(raw);
            if (!Number.isFinite(number)) return;
            if (name === 'DELAY' && /s$/i.test(raw)) updateSettings({delaySeconds: number});
            if (name === 'SIZE' && /%$/.test(raw)) updateSettings({sizePercent: number});
            if ((name.indexOf('VERTICAL') !== -1 || name.indexOf('POSIÇÃO') !== -1 ||
                    name.indexOf('POSITION') !== -1) && /%$/.test(raw))
                updateSettings({verticalPercent: number});
        });
    }

    window.addEventListener('stremiosrv:ai-subtitle-settings', function (event) {
        updateSettings(event && event.detail);
    });

    function video() {
        return document.querySelector('video');
    }

    function nativeClockReady() {
        // Desktop shells render video through MPV, not an HTMLVideoElement.
        // Only trusted Stremio UI clock events establish active native playback.
        return !video() && playerClock && Date.now() - playerClock.at <= 12000;
    }

    function playerPaused(media) {
        return media ? media.paused : !nativeClockReady() || !!playerClock.paused;
    }

    // Stremio's player time is an absolute movie position. HTMLMediaElement
    // currentTime can be relative to the current HLS/transcoded segment.
    // Never send a relative clock to the 30-second subtitle scheduler.
    function playbackSeconds(media) {
        if (!playerClock) return null;
        var age = (Date.now() - playerClock.at) / 1000;
        if (age < 0 || (age > 12 && !(media && media.paused) &&
                !playerClock.paused && !playerClock.buffering)) return null;
        if (playerClock.paused || playerClock.buffering || (media && media.paused))
            return playerClock.seconds;
        return playerClock.seconds + Math.min(age, 1.25) *
            ((media && Number.isFinite(media.playbackRate) && media.playbackRate > 0)
                ? media.playbackRate : 1);
    }

    window.addEventListener('stremiosrv:playback-clock', function (event) {
        var detail = event && event.detail;
        var time = detail && detail.time;
        if (typeof time !== 'number' || !Number.isFinite(time) ||
                time < 0 || time > 24 * 3600) return;
        var old = playerClock, now = Date.now();
        var wasBuffering = !!(old && old.buffering);
        var isBuffering = !!detail.buffering;
        // A buffering->playing transition may carry an old position. Require
        // a subsequent, advancing MPV clock before showing cached cues.
        if (!video()) {
            if (isBuffering) {
                nativeClockResuming = true;
                nativeClockResumeAt = null;
            } else if (wasBuffering) {
                nativeClockResuming = true;
                nativeClockResumeAt = time;
            } else if (nativeClockResuming && nativeClockResumeAt !== null &&
                       time > nativeClockResumeAt + 0.15) {
                nativeClockResuming = false;
                nativeClockResumeAt = null;
            }
        }
        playerClock = {seconds: time, at: now,
            paused: !!detail.paused, buffering: isBuffering};
        // The stream can change its absolute clock without firing seeking on
        // <video>; cancel requests based on the previous timeline.
        if (active && old && now - old.at < 12000 &&
                Math.abs(time - old.seconds) > 20) {
            playbackEpoch += 1;
            if (pendingPollController) pendingPollController.abort();
            cues = [];
            lastHeartbeat = 0;
            active.currentBlockReady = false;
            active.stage = 'preparing';
            if (!video()) {
                nativeClockResuming = true;
                nativeClockResumeAt = time;
            }
        }
        if (active) tick();
    });

    function selectedReference(track) {
        if (!track) return {reason: 'faixa ausente'};
        var candidate = track.url || track.fallbackUrl;
        if (typeof candidate !== 'string' || !candidate.trim())
            return {reason: 'sem URL na faixa seleccionada'};
        try {
            var reference = new URL(candidate, window.location.href);
            if (reference.protocol !== 'https:' && reference.protocol !== 'http:')
                return {reason: 'protocolo não suportado'};
            // The WebPlayer can wrap or convert subtitle URLs, including
            // in a query string. Search only for our *full* capability route,
            // with exact token, hash, index and cache-key structure.
            // Never use the outer origin as a network destination.
            var value = String(candidate);
            for (var attempt = 0; attempt < 5; attempt++) {
                var modern = value.match(
                    /\/ai-subtitles\/([0-9a-f]{64})\/placeholder\/([0-9a-f]{40})\/(\d+)\/([0-9a-f]{64})\.srt(?=$|[?#&\/])/i);
                var legacy = value.match(
                    /\/ai-subtitles\/([0-9a-f]{64})\/file\/([0-9a-f]{40})\/([0-9a-f]{64})\.srt(?=$|[?#&\/])/i);
                if (modern || legacy) {
                    var groups = modern || legacy;
                    return {track: {
                        base: window.location.origin + '/ai-subtitles/' + groups[1],
                        infohash: groups[2].toLowerCase(),
                        fileIndex: modern ? Number(groups[3]) : 0,
                        audioIndex: 0, legacy: !modern
                    }};
                }
                if (!/%[0-9a-f]{2}/i.test(value)) break;
                value = decodeURIComponent(value);
            }
            var first = reference.pathname.split('/').filter(Boolean)[0] || '(raiz)';
            if (!/^[a-zA-Z._-]{1,36}$/.test(first)) first = '(rota codificada)';
            return {reason: 'URL sem rota AI válida [rota=' + first +
                ', query=' + (reference.search ? 'sim' : 'não') +
                ', extensão=' + (/\.vtt(?:[?#]|$)/i.test(candidate) ? 'vtt' :
                    /\.srt(?:[?#]|$)/i.test(candidate) ? 'srt' : 'outra') + ']'};
        } catch (_) {
            return {reason: 'URL da faixa inválido'};
        }
    }

    function parseTrack(track) {
        return selectedReference(track).track || null;
    }

    function ensureOverlay() {
        if (view && view.parentNode) return view;
        view = document.createElement('div');
        view.id = 'stremiosrv-ai-subtitle-overlay';
        view.setAttribute('aria-live', 'polite');
        view.style.cssText = [
            'position:fixed', 'pointer-events:none', 'z-index:2147483000',
            'text-align:center', 'font-weight:600', 'font-size:20px',
            'line-height:1.45', 'color:#fff', 'text-shadow:0 2px 4px #000,0 0 8px #000',
            'white-space:pre-line', 'display:none', 'padding:0 10px',
            'box-sizing:border-box'
        ].join(';');
        document.body.appendChild(view);
        return view;
    }

    function render(message) {
        var element = ensureOverlay();
        var media = video();
        if (!active || !message || (media && media.paused && !active.startupBuffering) ||
                (!media && (!nativeClockReady() || playerClock.paused ||
                            playerClock.buffering || nativeClockResuming))) {
            element.style.display = 'none';
            return;
        }
        // Native MPV has no HTML <video> bounds. Use the visible player
        // viewport for a non-interactive overlay; normal WebPlayer geometry
        // is entirely unchanged.
        var bounds = media && media.getBoundingClientRect ?
            media.getBoundingClientRect() :
            {left:0,top:0,width:window.innerWidth||document.documentElement.clientWidth,
             height:window.innerHeight||document.documentElement.clientHeight,
             bottom:window.innerHeight||document.documentElement.clientHeight};
        if (bounds.width < 100 || bounds.height < 80) {
            element.style.display = 'none';
            return;
        }
        element.style.left = Math.round(bounds.left + 10) + 'px';
        // Match Stremio's native extra-subtitle renderer exactly: bottom
        // percentage within the video viewport, with size expressed in vmin.
        // The old top/20px calculation ignored the native position and size.
        element.style.top = 'auto';
        element.style.bottom = Math.round(
            Math.max(0, (window.innerHeight || bounds.bottom) - bounds.bottom) +
            bounds.height * styleSettings.verticalPercent / 100) + 'px';
        element.style.width = Math.round(Math.max(100, bounds.width - 20)) + 'px';
        var nativeScale = window.screen720p ? 1.538 : 1;
        var cueFontSize = Math.floor(
            styleSettings.sizePercent / 25 * nativeScale) + 'vmin';
        // The built-in renderer colours each subtitle cue, not the whole
        // viewport-wide overlay. A child span provides the same background,
        // text colour and 8-direction outline without an opaque video bar.
        element.textContent = '';
        var cue = document.createElement('span');
        cue.textContent = message;
        // Stremio sets font-size on each rendered subtitle cue, not the
        // wrapping viewport. Match the same DOM node and display context.
        cue.style.display = 'inline-block';
        cue.style.fontSize = cueFontSize;
        cue.style.whiteSpace = 'pre-wrap';
        cue.style.padding = '0.2em';
        cue.style.backgroundColor = styleSettings.backgroundColor;
        cue.style.color = styleSettings.textColor;
        cue.style.opacity = String(styleSettings.opacityPercent / 100);
        cue.style.boxDecorationBreak = 'clone';
        cue.style.webkitBoxDecorationBreak = 'clone';
        var outline = styleSettings.outlineColor;
        cue.style.textShadow = [
            '-0.15rem -0.15rem 0.15rem ',
            '0 -0.15rem 0.15rem ',
            '0.15rem -0.15rem 0.15rem ',
            '-0.15rem 0 0.15rem ',
            '0.15rem 0 0.15rem ',
            '-0.15rem 0.15rem 0.15rem ',
            '0 0.15rem 0.15rem ',
            '0.15rem 0.15rem 0.15rem '
        ].map(function (part) { return part + outline; }).join(', ');
        element.appendChild(cue);
        element.style.display = 'block';
    }

    function readCues(text) {
        var result = [];
        var regex = /(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})/;
        function seconds(parts, offset) {
            return Number(parts[offset])*3600 + Number(parts[offset+1])*60 +
                Number(parts[offset+2]) + Number(parts[offset+3])/1000;
        }
        String(text || '').split(/\r?\n\s*\r?\n/).forEach(function (block) {
            var lines = block.split(/\r?\n/);
            var index = lines.findIndex(function (line) { return regex.test(line); });
            if (index < 0) return;
            var match = regex.exec(lines[index]);
            var value = lines.slice(index + 1).join('\n').trim();
            if (value) result.push({ start: seconds(match, 1), end: seconds(match, 5), text: value });
        });
        return result;
    }

    function diagnostic(track, event, httpStatus) {
        if (!track) return;
        // This request never starts work. Server stores only the event name
        // and status code; the addon token never appears in diagnostic data.
        fetch(track.base + '/demand/client-event', {
            method: 'POST', cache: 'no-store',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ event: event, httpStatus: httpStatus || 0 })
        }).catch(function () {});
    }

    function sendStop(old) {
        if (!old || !old.sessionId) return;
        fetch(old.track.base + '/demand/' + encodeURIComponent(old.sessionId) + '/stop', {
            method: 'POST', cache: 'no-store', keepalive: true
        }).catch(function () {});
    }

    function stop(resumePlayback) {
        generation += 1;
        playbackEpoch += 1;
        if (pendingPollController) pendingPollController.abort();
        if (pendingStartController) pendingStartController.abort();
        var previous = active;
        var media = video();
        var shouldResume = (resumePlayback !== false && previous &&
            previous.startupBuffering && previous.resumeWanted &&
            media && typeof media.play === 'function');
        active = null;
        cues = [];
        lastHeartbeat = 0;
        lastSubtitlePoll = 0;
        if (previous && previous.track) diagnostic(previous.track, 'stopped');
        sendStop(previous);
        if (view) view.style.display = 'none';
        if (shouldResume) media.play().catch(function () {});
    }

    async function begin() {
        if (!active || active.sessionId || active.starting ||
                (active.retryAfter && Date.now() < active.retryAfter)) return;
        var media = video();
        if (media ? (media.readyState < 1 ||
                (media.paused && !active.startupBuffering) || media.ended) :
                (!nativeClockReady() || playerClock.paused || playerClock.buffering)) return;
        var position = playbackSeconds(media);
        if (position === null) return;
        var mine = active, stamp = generation;
        var controller = typeof AbortController === 'function' ? new AbortController() : null;
        pendingStartController = controller;
        var timeoutId = controller && typeof setTimeout === 'function' ?
            setTimeout(function () { controller.abort(); }, 12000) : null;
        mine.starting = true;
        try {
            var res = await fetch(mine.track.base + '/demand/start', {
                method: 'POST', cache: 'no-store',
                headers: { 'Content-Type': 'application/json' },
                signal: controller ? controller.signal : undefined,
                body: JSON.stringify({
                    infohash: mine.track.infohash, fileIndex: mine.track.fileIndex,
                    audioIndex: mine.track.audioIndex, positionSeconds: position
                })
            });
            if (!res.ok) {
                diagnostic(mine.track, 'start_failed', res.status);
                throw new Error('AI subtitle start: HTTP ' + res.status);
            }
            var data = await res.json();
            if (active !== mine || stamp !== generation) {
                sendStop({ sessionId: data.sessionId, track: mine.track });
                return;
            }
            mine.sessionId = data.sessionId;
            mine.retryAfter = 0;
            mine.error = null;
            diagnostic(mine.track, 'started');
            mine.stage = 'preparing';
            lastHeartbeat = 0;
            lastSubtitlePoll = 0;
        } catch (err) {
            if (active === mine && stamp === generation) {
                mine.stage = 'error';
                mine.error = 'Não foi possível iniciar as legendas AI (' +
                    String(err.message || 'erro de ligação').slice(0, 70) + ').';
                mine.retryAfter = Date.now() + 15000;
                releaseStartup(mine);
                console.warn(err);
            }
        } finally {
            if (timeoutId !== null && typeof clearTimeout === 'function')
                clearTimeout(timeoutId);
            if (pendingStartController === controller) pendingStartController = null;
            mine.starting = false;
        }
    }

    function releaseStartup(mine) {
        if (!mine.startupBuffering) return;
        mine.startupBuffering = false;
        if (mine.resumeWanted && active === mine) {
            var media = video();
            if (media && typeof media.play === 'function')
                media.play().catch(function () {});
        }
        mine.resumeWanted = false;
    }

    async function pollStatus(mine, seconds) {
        if (!mine.sessionId || requestBusy) return;
        var epoch = playbackEpoch;
        var sessionId = mine.sessionId;
        var controller = typeof AbortController === 'function' ? new AbortController() : null;
        pendingPollController = controller;
        // Bound an unresponsive transport, or a dead connection can block all future polls.
        var timeoutId = controller && typeof setTimeout === 'function' ?
            setTimeout(function () { controller.abort(); }, 12000) : null;
        requestBusy = true;
        try {
            var heartbeat = await fetch(
                mine.track.base + '/demand/' + encodeURIComponent(sessionId) + '/heartbeat', {
                    method: 'POST', cache: 'no-store',
                    headers: { 'Content-Type': 'application/json' },
                    signal: controller ? controller.signal : undefined,
                    body: JSON.stringify({ positionSeconds: seconds })
                }
            );
            if (active !== mine || epoch !== playbackEpoch ||
                    sessionId !== mine.sessionId) return;
            if (!heartbeat.ok) {
                diagnostic(mine.track, 'heartbeat_failed', heartbeat.status);
                if (heartbeat.status === 404) {
                    mine.sessionId = null;
                    mine.stage = 'preparing';
                    begin();
                }
                return;
            }
            var status = await heartbeat.json();
            if (active !== mine || epoch !== playbackEpoch ||
                    sessionId !== mine.sessionId) return;
            mine.stage = status.stage;
            mine.currentBlockReady = !!status.currentBlockReady;
            mine.bufferedUntilSeconds = status.bufferedUntilSeconds;
            mine.error = status.error;
            if (mine.startupBuffering) {
                var media = video();
                var movieTime = playbackSeconds(media);
                // Do not resume until the initial 30-second window is covered.
                // On persistent errors give control back to the user.
                if ((status.currentBlockReady && movieTime !== null &&
                     status.bufferedUntilSeconds >= movieTime + 30) ||
                    status.stage === 'error' ||
                    Date.now() - mine.startedAt > 45000) {
                    releaseStartup(mine);
                }
            }
            var res = await fetch(
                mine.track.base + '/demand/' + encodeURIComponent(sessionId) +
                    '/subtitles.srt?ts=' + Date.now(), {
                        cache: 'no-store', signal: controller ? controller.signal : undefined
                    });
            if (active === mine && epoch === playbackEpoch &&
                    sessionId === mine.sessionId && res.ok) {
                var next = readCues(await res.text());
                if (active !== mine || epoch !== playbackEpoch ||
                        sessionId !== mine.sessionId) return;
                if (next.length && (!cues.length ||
                        next.length !== cues.length)) {
                    diagnostic(mine.track, 'srt_received');
                }
                cues = next;
            }
        } catch (err) {
            if (!err || err.name !== 'AbortError')
                console.warn('AI subtitles unavailable:', err);
        } finally {
            if (timeoutId !== null && typeof clearTimeout === 'function')
                clearTimeout(timeoutId);
            if (pendingPollController === controller) pendingPollController = null;
            requestBusy = false;
        }
    }

    function tick() {
        if (!active) return;
        observeNativeSettings();
        var media = video();
        // Selection can precede metadata: wait for a usable playhead instead of
        // discarding the user's AI choice before the first frame loads.
        if (media ? media.readyState < 1 : !nativeClockReady()) {
            render('');
            return;
        }
        var movieTime = playbackSeconds(media);
        if (movieTime === null) {
            render('A sincronizar o relógio do Stremio…');
            return;
        }
        if (media && media.ended) {
            stop(false);
            return;
        }
        // Pausing ends the active job for THIS player, not the shared cache.
        if (playerPaused(media) && !active.startupBuffering) {
            if (!active.pausedForUser) {
                active.pausedForUser = true;
                generation += 1;
                playbackEpoch += 1;
                if (pendingStartController) pendingStartController.abort();
                if (pendingPollController) pendingPollController.abort();
                if (active.sessionId) sendStop(active);
                active.sessionId = null;
                cues = [];
                lastHeartbeat = 0;
            }
            render('');
            return;
        }
        if (active.pausedForUser) {
            active.pausedForUser = false;
            active.startedAt = Date.now();
            active.stage = 'preparing';
            active.retryAfter = 0;
        }
        if (!active.sessionId) begin();
        var now = Date.now();
        if (active.sessionId && now - lastHeartbeat >= 5000) {
            lastHeartbeat = now;
            pollStatus(active, movieTime);
        }
        var cue = cues.find(function (item) {
            return item.start <= movieTime - styleSettings.delaySeconds &&
                item.end >= movieTime - styleSettings.delaySeconds;
        });
        // An earlier block can be ready while the playhead's new block is
        // not. Keep the preparation notice visible until the *current*
        // block is available; never hide it merely because stage == ready.
        var message = active.startupBuffering
            ? 'A preparar legendas AI em português…'
            : (cue ? cue.text : (active.currentBlockReady ? '' :
            (active.error || (active.stage === 'buffering_audio'
                ? 'Legendas AI: a aguardar áudio do torrent…'
                : 'A preparar legendas AI em português…'))));
        render(message);
    }

    window.addEventListener('stremiosrv:subtitle-selection', function (event) {
        var track = parseTrack(event.detail && event.detail.track);
        if (track && active && active.track &&
                active.track.infohash === track.infohash &&
                active.track.fileIndex === track.fileIndex &&
                active.track.audioIndex === track.audioIndex &&
                active.track.base === track.base) return;
        stop();
        if (!track) {
            var selected = event.detail && event.detail.track;
            var identity = selected && String((selected.name || '') + ' ' + (selected.id || ''));
            if (identity && /ai.subtitle|ai-demand/i.test(identity)) {
                // Explicit, non-sensitive failure indicator. The WebPlayer
                // may provide an ID but no subtitle URL; never silently
                // claim that OpenVINO is processing in that case.
                var warning = ensureOverlay();
                warning.textContent = 'AI: ' + selectedReference(selected).reason +
                    '. Verificar ligação do addon.';
                warning.style.left = '10%';
                warning.style.top = '75%';
                warning.style.width = '80%';
                warning.style.display = 'block';
                console.warn('AI subtitle track has no valid server URL',
                    {hasId: !!selected.id, hasUrl: !!selected.url});
            }
            return;
        }
        diagnostic(track, 'selected');
        active = { track: track, sessionId: null, starting: false, stage: 'preparing',
            startupBuffering: false, resumeWanted: false, startedAt: Date.now() };
        var media = video();
        if (media && !media.paused && typeof media.pause === 'function') {
            // Pause only when *we* initiated it; respect an existing user pause.
            active.startupBuffering = true;
            active.resumeWanted = true;
            media.pause();
        }
        tick();
    });
    window.addEventListener('stremio-webadmin:player-unload', function () { stop(false); });
    window.addEventListener('pagehide', function () { stop(false); });
    // Switching to another window to inspect GPU/logs does not mean the
    // playback stopped. Retain the AI selection and session while the video
    // is actually playing; stop() still handles pagehide/player unload and
    // the backend expires abandoned sessions without heartbeats.
    document.addEventListener('visibilitychange', function () {
        if (!active) return;
        var media = video();
        if ((media && media.ended) || (!media && !nativeClockReady())) {
            stop(false);
        } else if (document.hidden && playerPaused(media) && !active.startupBuffering) {
            stop(false);
        } else if (!playerPaused(media)) {
            // Refresh immediately when returning from a throttled background tab.
            lastHeartbeat = 0;
            tick();
        }
    });
    // The media clock, not a wall-clock interval, governs subtitle rendering.
    // Capture timeupdate/seeked from the active <video> without additional
    // heartbeat requests; tick() remains rate-limited at 5s for the backend.
    function onVideoClock(event) {
        if (!active || !event.target || event.target !== video()) return;
        tick();
    }
    document.addEventListener('timeupdate', onVideoClock, true);
    document.addEventListener('seeking', function (event) {
        if (!active || event.target !== video()) return;
        playbackEpoch += 1;
        if (pendingPollController) pendingPollController.abort();
        lastHeartbeat = 0;
        cues = [];
        active.currentBlockReady = false;
        active.stage = 'preparing';
        if (view) view.style.display = 'none';
    }, true);
    document.addEventListener('seeked', onVideoClock, true);
    document.addEventListener('ratechange', onVideoClock, true);
    setInterval(tick, 1000);
})();

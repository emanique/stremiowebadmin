// The bundled web player's loader: seeds the streaming-server URL, then leaves the user's pick alone.
//
// docker/install-web-player-loader.sh installs this over the web build's own loader.js when the image
// is built. That one (load_localStorage.js in the base image's fork) re-applied the seeded URL every
// 5 s and reloaded the page, so a URL picked in Settings snapped back within seconds -- and with no
// SERVER_URL the seed is 127.0.0.1:11470, which no browser on another machine can reach or escape.
//
// The seed is the first streaming server in localStorage.json, which the entrypoint rewrites from
// SERVER_URL; when server_url.env does not answer, it is the page's own origin. The seed goes in when:
//   - the browser has no profile or no URL yet (a first visit);
//   - the stored URL is the app's own default, 127.0.0.1:11470, and the seed is something else. The
//     app writes that default when it boots before this script has seeded it, and after a profile
//     reset; it is never this server, so this check keeps running;
//   - the seed differs from the one this browser was last given: the operator changed SERVER_URL.
//     That is applied once, then the user's choices stand again.
// Any other URL is one the user picked, and it stays. Picking 127.0.0.1:11470 itself does not stick
// while a SERVER_URL is set: it is indistinguishable from the app's default.
(function () {
    'use strict';

    var APP_DEFAULT = 'http://127.0.0.1:11470/';
    var SEEDED = 'stremiosrv_seeded_server_url';  // the seed this browser was last given
    var CHECK_EVERY_MS = 5000;

    var seedFile = null;
    var seed = null;
    var seedStamp = null;
    var lastAddonAuthKey = null;

    function read(key) {
        try {
            return JSON.parse(localStorage.getItem(key));
        } catch (e) {
            return null;
        }
    }

    function write(key, value) {
        localStorage.setItem(key, JSON.stringify(value));
    }

    function pageOrigin() {
        var url = window.location.href;
        var start = url.indexOf('://') + 3;
        var end = url.indexOf('/', start);
        return (end === -1 ? url : url.substring(0, end)) + '/';
    }

    function storedUrl() {
        var profile = read('profile');
        return profile && profile.settings ? profile.settings.streamingServerUrl || null : null;
    }

    function wantsSeed(stored) {
        if (!stored) {
            return true;
        }
        if (stored === APP_DEFAULT && seed !== APP_DEFAULT) {
            return true;
        }
        return localStorage.getItem(SEEDED) !== seed;
    }

    // Puts the seed in; returns whether the running app holds something else in memory (a reload).
    function applySeed() {
        var reload = false;

        // A first visit: every other key the seed file carries, as it is.
        Object.keys(seedFile).forEach(function (key) {
            if (localStorage.getItem(key) === null && key !== 'streaming_server_urls' &&
                    key !== 'profile') {
                write(key, seedFile[key]);
            }
        });

        // The saved servers: add the seed, keep the ones the user added.
        var servers = read('streaming_server_urls');
        if (!servers || typeof servers.items !== 'object' || servers.items === null) {
            write('streaming_server_urls', { uid: null, items: (function () {
                var items = {};
                items[seed] = seedStamp;
                return items;
            })() });
        } else if (!Object.prototype.hasOwnProperty.call(servers.items, seed)) {
            servers.items[seed] = seedStamp;
            write('streaming_server_urls', servers);
            reload = true;
        }

        var profile = read('profile');
        if (profile === null && seedFile.profile && seedFile.profile.settings) {
            // A first visit: the seed file's own profile, which is complete -- the app refuses to
            // start on a profile with settings missing.
            profile = seedFile.profile;
            profile.settings.streamingServerUrl = seed;
            write('profile', profile);
        } else if (profile && profile.settings && profile.settings.streamingServerUrl !== seed) {
            profile.settings.streamingServerUrl = seed;
            write('profile', profile);
            reload = true;
        }

        localStorage.setItem(SEEDED, seed);
        return reload;
    }

    function check() {
        try {
            if (!wantsSeed(storedUrl())) {
                return;
            }
            if (applySeed()) {
                console.log('Streaming server URL set to ' + seed + ', reloading page ...');
                window.location.reload();
            }
        } catch (e) {
            console.error('Could not apply the seeded streaming server URL:', e);
        }
    }

    // My Library belongs to the Stremio ACCOUNT, not to this browser. Check it whenever the
    // player acquires a different authKey (initial login, logout/login, or account switch). The
    // server reads the current remote addon collection first, so this is idempotent and also
    // replaces an old My Library descriptor whose token/URL no longer matches this server.
    async function ensureLibraryAddon() {
        var profile = read('profile');
        var key = profile && profile.auth ? profile.auth.key || null : null;
        if (!key) {
            lastAddonAuthKey = null;
            return;
        }
        if (key === lastAddonAuthKey) {
            return;
        }
        // Mark before the request so a slow API cannot create overlapping collection writes. A
        // failure is retried on the next page load/login, rather than every five seconds forever.
        lastAddonAuthKey = key;
        try {
            var resp = await fetch('/library/api/addon/ensure', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ authKey: key })
            });
            if (!resp.ok) {
                throw new Error(resp.status + ' ' + resp.statusText);
            }
            var result = await resp.json();
            if (result.allowed === false) {
                console.log('My Library not installed: this Stremio account is not allowed by the server.');
            } else {
                console.log(result.changed ? 'My Library synced to this Stremio account.'
                                           : 'My Library already installed for this Stremio account.');
            }
        } catch (e) {
            console.error('Could not sync My Library to this Stremio account:', e);
        }
    }

    // Track HLS jobs used by the SPA. The upstream web player creates /hlsv2 jobs but does not
    // call the server's /destroy endpoint when its logical player is closed. Since the SPA remains
    // loaded, page unload cannot be relied on and ffmpeg can otherwise continue reading the source.
    var hlsJobs = {};
    var HLS_JOB_RE = /\/hlsv2\/([^/?#]+)\/(?:master\.m3u8|index\.m3u8|init\.mp4|seg\d+\.m4s)/;

    function rememberHlsUrl(value) {
        try {
            var match = String(value || '').match(HLS_JOB_RE);
            if (match) {
                hlsJobs[decodeURIComponent(match[1])] = true;
            }
        } catch (e) {
            console.warn('Could not track HLS job:', e);
        }
    }

    function destroyHlsJobs() {
        var jobs = Object.keys(hlsJobs);
        hlsJobs = {};
        jobs.forEach(function (jobId) {
            // keepalive lets the request finish while the SPA is changing view or the page is closing.
            fetch('/hlsv2/' + encodeURIComponent(jobId) + '/destroy', {
                method: 'GET',
                credentials: 'same-origin',
                cache: 'no-store',
                keepalive: true
            }).catch(function (e) {
                console.warn('Could not destroy HLS job ' + jobId + ':', e);
            });
        });
    }

    // Resource Timing sees HLS requests made by the media stack as well as ordinary fetch/XHR.
    if (typeof PerformanceObserver !== 'undefined') {
        try {
            var hlsObserver = new PerformanceObserver(function (list) {
                list.getEntries().forEach(function (entry) { rememberHlsUrl(entry.name); });
            });
            hlsObserver.observe({ type: 'resource', buffered: true });
        } catch (e) {
            console.warn('Could not observe HLS resources:', e);
        }
    }

    // Page close remains a last-resort cleanup. Logical player unload is patched directly in
    // the bundled player so hls.js internal media attach/detach cannot be mistaken for playback end.

    if (window.addEventListener) {
        window.addEventListener('stremio-webadmin:player-unload', destroyHlsJobs);
        window.addEventListener('pagehide', destroyHlsJobs);
    }

    async function start() {
        try {
            var resp = await fetch('localStorage.json');
            if (!resp.ok) {
                throw new Error(resp.status + ' ' + resp.statusText);
            }
            seedFile = await resp.json();
            var env = await fetch('server_url.env', { method: 'HEAD' });
            var items = (seedFile.streaming_server_urls && seedFile.streaming_server_urls.items) || {};
            if (env.ok && Object.keys(items).length) {
                seed = Object.keys(items)[0];
                seedStamp = items[seed];
            } else {
                seed = pageOrigin();
                seedStamp = new Date().toISOString();
            }
        } catch (e) {
            console.error('Error loading the web player seed (localStorage.json):', e);
            return;
        }
        console.log('Seeded streaming server URL:', seed);
        check();
        ensureLibraryAddon();
        setInterval(function () {
            check();
            ensureLibraryAddon();
        }, CHECK_EVERY_MS);
    }

    start();
})();

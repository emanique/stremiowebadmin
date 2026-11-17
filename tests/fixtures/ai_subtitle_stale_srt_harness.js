'use strict';
// Deterministic regression: a slow subtitle response from before a seek
// must never repopulate captions after the playhead has moved.
const fs = require('fs');
const vm = require('vm');
const script = fs.readFileSync(process.argv[2], 'utf8');
const token = 'a'.repeat(64), infohash = 'b'.repeat(40), key = 'c'.repeat(64);
const trackUrl = 'https://stremio.example/ai-subtitles/' + token +
  '/placeholder/' + infohash + '/0/' + key + '.srt';
const handlers = {}, timers = [], calls = [], overlays = [];
let resolveOldSubtitle;
let holdFirstSubtitle = true;
const video = {
  currentTime: 150, readyState: 4, paused: false, ended: false,
  pause() { this.paused = true; },
  play() { this.paused = false; return Promise.resolve(); },
  getBoundingClientRect() { return {left: 0, top: 0, bottom: 400, width: 800, height: 400}; }
};
const body = {appendChild(el) { el.parentNode = body; }};
const document = {
  body, hidden: false,
  querySelector() { return video; },
  addEventListener(event, callback) { (handlers[event] ||= []).push(callback); },
  createElement() {
    const el = {style: {}, setAttribute() {}, textContent: '', parentNode: null};
    overlays.push(el); return el;
  }
};
const fetch = async (url, options = {}) => {
  calls.push([String(url), options.method || 'GET']);
  if (url.endsWith('/demand/start')) {
    return {ok: true, json: async () => ({sessionId: 'test-session'})};
  }
  if (url.includes('/client-event')) return {ok: true};
  if (url.includes('/heartbeat')) return {ok: true, json: async () =>
    ({stage: 'ready', currentBlockReady: true, bufferedUntilSeconds: 999})};
  if (url.includes('/subtitles.srt')) {
    if (holdFirstSubtitle) {
      return {ok: true, text: () => new Promise(resolve => {
        resolveOldSubtitle = resolve;
      })};
    }
    return {ok: true, text: async () =>
      '1\n00:03:20,000 --> 00:03:25,000\nCURRENT\n'};
  }
  if (url.includes('/stop')) return {ok: true};
  throw new Error('unexpected fetch ' + url);
};
const sandbox = {
  window: null, document, fetch, console,
  setInterval: fn => timers.push(fn), URL, Date, Number, String, Array, JSON, Math,
  location: {href: 'https://stremio.example/', origin: 'https://stremio.example'}
};
sandbox.window = sandbox;
sandbox.addEventListener = (name, callback) => (handlers[name] ||= []).push(callback);
vm.createContext(sandbox);
vm.runInContext(script, sandbox);
const emit = (name, detail, target) =>
  (handlers[name] || []).forEach(fn => fn({detail, target}));
const flush = async () => {
  for (let i = 0; i < 12; i++) await new Promise(resolve => setImmediate(resolve));
};
(async () => {
  emit('stremiosrv:playback-clock', {time: 150, paused:false,buffering:false});
  emit('stremiosrv:subtitle-selection', {track: {url: trackUrl}});
  await flush();
  timers.forEach(fn => fn());
  await flush();
  if (!resolveOldSubtitle) throw new Error('test did not hold old SRT');
  video.currentTime = 200;
  emit('seeking', null, video);
  holdFirstSubtitle = false;
  resolveOldSubtitle('1\n00:02:30,000 --> 00:02:45,000\nSTALE\n');
  await flush();
  timers.forEach(fn => fn());
  await flush();
  const overlay = overlays[0];
  if (!overlay || overlay.textContent === 'STALE')
    throw new Error('obsolete subtitles displayed after seeking');
  emit('stremiosrv:subtitle-selection', {track: null});
  await flush();
  if (calls.filter(([u]) => u.endsWith('/demand/start')).length !== 1)
    throw new Error('unexpected AI generation sessions');
  if (calls.filter(([u]) => u.endsWith('/stop')).length !== 1)
    throw new Error('AI session not stopped');
  process.stdout.write(JSON.stringify({staleRejected: true, starts: 1, stops: 1}));
})().catch(err => {console.error(err); process.exitCode = 1;});

'use strict';
// Pause/resume and stale /demand/start lifecycle regression test.
const fs = require('fs'), vm = require('vm');
const script = fs.readFileSync(process.argv[2], 'utf8');
const events = {}, ticks = [], calls = [];
const url = 'https://stremio.example/ai-subtitles/' + 'a'.repeat(64) +
  '/placeholder/' + 'b'.repeat(40) + '/0/' + 'c'.repeat(64) + '.srt';
const player = {
  currentTime: 100, readyState: 4, paused: true, ended: false,
  pause() { this.paused = true; },
  play() { this.paused = false; return Promise.resolve(); },
  getBoundingClientRect() { return {left: 0, top: 0, bottom: 400, width: 800, height: 400}; }
};
const body = {appendChild(el) { el.parentNode = body; }};
const document = {
  body, hidden: false, querySelector: () => player,
  createElement: () => ({style: {}, setAttribute() {}, textContent: '', parentNode: null}),
  addEventListener: (name, fn) => (events[name] ||= []).push(fn),
};
let resolveStart, startCount = 0, abortCount = 0;
class TestAbortController {
  constructor() { this.signal = {}; }
  abort() { abortCount += 1; }
}
const fetch = async (address, options = {}) => {
  calls.push([String(address), options.method || 'GET']);
  if (String(address).endsWith('/demand/start')) {
    startCount += 1;
    if (startCount === 1) return new Promise(resolve => {resolveStart = resolve;});
    return {ok: true, json: async () => ({sessionId: 'new-session'})};
  }
  if (String(address).includes('/client-event') || String(address).includes('/stop')) {
    return {ok: true};
  }
  if (String(address).endsWith('/heartbeat')) return {
    ok: true, json: async () => ({stage: 'ready', currentBlockReady: true, bufferedUntilSeconds: 999})
  };
  if (String(address).includes('/subtitles.srt')) return {ok: true, text: async () => ''};
  throw new Error('unexpected fetch: ' + address);
};
const context = {
  window: null, document, console, fetch,
  setInterval: fn => ticks.push(fn), setTimeout: () => 1, clearTimeout() {},
  AbortController: TestAbortController, URL, Date, Number, String, Array, JSON, Math,
  location: {href: 'https://stremio.example/', origin: 'https://stremio.example'}
};
context.window = context;
context.addEventListener = (name, fn) => (events[name] ||= []).push(fn);
vm.createContext(context);
vm.runInContext(script, context);
const emit = (name, detail) => (events[name] || []).forEach(fn => fn({detail}));
const flush = async () => {
  for (let i = 0; i < 12; i++) await new Promise(resolve => setImmediate(resolve));
};
(async () => {
  emit('stremiosrv:playback-clock', {time: 100, paused:true,buffering:false});
  emit('stremiosrv:subtitle-selection', {track: {url}});
  await flush();
  player.paused = false;
  ticks.forEach(fn => fn());
  await flush();
  if (!resolveStart) throw new Error('first start was not requested');
  player.paused = true;
  ticks.forEach(fn => fn());
  await flush();
  if (abortCount < 1) throw new Error('pause did not abort start request');
  resolveStart({ok: true, json: async () => ({sessionId: 'old-session'})});
  await flush();
  if (calls.filter(([x]) => x.includes('/old-session/stop')).length !== 1) {
    throw new Error('late start session not cancelled');
  }
  player.paused = false;
  ticks.forEach(fn => fn());
  await flush();
  if (startCount !== 2) throw new Error('resume did not create new start session');
  emit('stremiosrv:subtitle-selection', {track: null});
  await flush();
  if (calls.filter(([x]) => x.includes('/new-session/stop')).length !== 1) {
    throw new Error('new session not closed on OFF');
  }
  process.stdout.write(JSON.stringify({pauseAbort: true, staleStartStopped: true,
    resumeStarts: startCount, offStops: true}));
})().catch(err => {console.error(err); process.exitCode = 1;});

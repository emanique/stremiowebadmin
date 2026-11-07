// Runs a web-player loader against a fake browser and prints what it did, as JSON.
//
//   node web_player_loader_harness.js <loader.js>  < scenario.json
//
// scenario: {seedFile: <localStorage.json>, envOk: <server_url.env answers 2xx>, origin: <page URL>,
//            storage: {key: value} (the browser's localStorage before the page loads),
//            afterLoad: ["tick" | {"set": [key, value]}, ...]}
// "tick" runs the loader's interval callbacks once (5 s passing); "set" writes a key the way the app
// persists it (a URL picked in Settings). A reload is counted, not performed: the page would start
// over, and the test runs the loader again for that.
'use strict';
const fs = require('fs');
const vm = require('vm');

const loaderPath = process.argv[2];
const scenario = JSON.parse(fs.readFileSync(0, 'utf8'));
const asText = (v) => (typeof v === 'string' ? v : JSON.stringify(v));

const store = new Map(Object.entries(scenario.storage || {}).map(([k, v]) => [k, asText(v)]));
const localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => { store.set(k, String(v)); },
  removeItem: (k) => { store.delete(k); },
};

let reloads = 0;
const intervals = [];
const events = {};
const requests = [];
let performanceCallback = null;
const fetch = async (url, opts = {}) => {
  requests.push({ url: String(url), method: opts.method || 'GET' });
  if (url === 'localStorage.json') {
    return { ok: true, status: 200, statusText: 'OK', json: async () => JSON.parse(JSON.stringify(scenario.seedFile)) };
  }
  if (url === 'server_url.env' && (opts.method || 'GET') === 'HEAD') {
    return scenario.envOk ? { ok: true, status: 200, statusText: 'OK' } : { ok: false, status: 404, statusText: 'Not Found' };
  }
  if (String(url).match(/^\/hlsv2\/[^/]+\/destroy$/)) {
    return { ok: true, status: 200, statusText: 'OK' };
  }
  throw new Error(`unexpected fetch: ${url}`);
};

function HTMLMediaElement() {}
HTMLMediaElement.prototype.removeAttribute = function (name) {
  if (String(name).toLowerCase() === 'src') this.src = '';
};
const video = new HTMLMediaElement();
video.tagName = 'VIDEO';

const sandbox = {
  localStorage,
  fetch,
  HTMLMediaElement,
  console: { log() {}, warn() {}, error() {} },
  setInterval: (fn) => { intervals.push(fn); return intervals.length; },
  addEventListener: (name, fn) => { (events[name] ||= []).push(fn); },
  PerformanceObserver: class {
    constructor(fn) { performanceCallback = fn; }
    observe() {}
  },
  location: { href: scenario.origin, reload: () => { reloads += 1; } },
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(loaderPath, 'utf8'), sandbox, { filename: 'loader.js' });

const settle = async () => { for (let i = 0; i < 10; i += 1) await new Promise((r) => setImmediate(r)); };

(async () => {
  await settle();  // the loader's own first pass: two fetches, then its check
  for (const step of scenario.afterLoad || []) {
    if (step === 'tick') intervals.forEach((fn) => fn());
    else if (step.set) localStorage.setItem(step.set[0], asText(step.set[1]));
    else if (step.resource && performanceCallback) {
      performanceCallback({ getEntries: () => [{ name: step.resource }] });
    } else if (step.event) {
      (events[step.event] || []).forEach((fn) => fn());
    } else if (step.videoUnload) {
      video.removeAttribute('src');
    }
    await settle();
  }
  const storage = {};
  for (const [k, v] of store) {
    try { storage[k] = JSON.parse(v); } catch (e) { storage[k] = v; }
  }
  process.stdout.write(JSON.stringify({ reloads, intervals: intervals.length, storage, requests }));
})();

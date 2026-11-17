'use strict';
// A transcoded <video> may expose segment-relative currentTime (70s) while
// the Stremio player reports the actual movie position (1200s).
const fs = require('fs'), vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');
const events = {}, ticks = [], requests = [], elems = [];
const media = {
  currentTime: 70, readyState: 4, paused: false, ended: false, playbackRate: 1,
  pause() { this.paused = true; },
  play() { this.paused = false; return Promise.resolve(); },
  getBoundingClientRect() { return {left:0, top:0, bottom:400, width:800, height:400}; }
};
const body = {appendChild(el) { el.parentNode = body; }};
const document = {
  body, hidden:false, querySelector: () => media,
  createElement: () => {
    const el = {style:{}, setAttribute(){}, textContent:'', parentNode:null};
    elems.push(el);return el;
  },
  addEventListener:(name,fn) => (events[name] ||= []).push(fn)
};
const fetch = async (url, options={}) => {
  requests.push({url:String(url),body:options.body});
  if (String(url).endsWith('/demand/start')) return {
    ok:true, json:async()=>({sessionId:'absolute-clock-test'})};
  if (String(url).includes('/client-event') || String(url).endsWith('/stop'))
    return {ok:true};
  if (String(url).endsWith('/heartbeat')) return {
    ok:true,json:async()=>({stage:'ready',currentBlockReady:true,
      bufferedUntilSeconds:1260})};
  if (String(url).includes('/subtitles.srt')) return {
    ok:true,text:async()=>('1\n00:20:00,000 --> 00:20:05,000\nALIGNED\n')};
  throw Error('unexpected URL '+url);
};
const context = {
  document, fetch, console, URL, Date, Number, String, Array, JSON, Math,
  setInterval: fn => ticks.push(fn),
  setTimeout:() => 1, clearTimeout(){},
  window:null, location:{href:'https://stremio.example/',origin:'https://stremio.example'}
};
context.window=context;
context.addEventListener=(name,fn) => (events[name] ||= []).push(fn);
vm.createContext(context);
vm.runInContext(src,context);
const emit=(name,detail)=>(events[name]||[]).forEach(fn=>fn({detail}));
const flush=async()=>{for(let i=0;i<14;i++)await new Promise(r=>setImmediate(r));};
(async()=>{
  const url='https://stremio.example/ai-subtitles/'+'a'.repeat(64)+
    '/placeholder/'+'b'.repeat(40)+'/0/'+'c'.repeat(64)+'.srt';
  emit('stremiosrv:subtitle-selection',{track:{url}});
  await flush();
  if (requests.some(x=>x.url.endsWith('/demand/start')))
    throw Error('started work without a trusted Stremio clock');
  emit('stremiosrv:playback-clock',{time:1200,paused:false,buffering:false});
  await flush();
  ticks.forEach(fn=>fn());
  await flush();
  const starts=requests.filter(x=>x.url.endsWith('/demand/start'));
  if(starts.length!==1)throw Error('start absent after player clock');
  if (Math.abs(JSON.parse(starts[0].body).positionSeconds-1200)>3)
    throw Error('used relative video clock for /start: '+starts[0].body);
  ticks.forEach(fn=>fn());
  await flush();
  const heartbeats=requests.filter(x=>x.url.endsWith('/heartbeat'));
  if(!heartbeats.length ||
     Math.abs(JSON.parse(heartbeats[0].body).positionSeconds-1200)>3)
    throw Error('used relative video clock for heartbeat');
  ticks.forEach(fn=>fn());
  await flush();
  if(!elems.some(x=>x.textContent==='ALIGNED'))
    throw Error('absolute timestamp not rendered at movie time 1200s');
  emit('stremiosrv:subtitle-selection',{track:null});
  await flush();
  process.stdout.write(JSON.stringify({trustedStart:true,trustedHeartbeat:true,
    absoluteCueRendered:true,relativeMediaTime:70,absoluteMovieTime:1200}));
})().catch(e=>{console.error(e);process.exitCode=1;});

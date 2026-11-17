'use strict';
const fs=require('fs'),vm=require('vm');
const script=fs.readFileSync(process.argv[2],'utf8');
const route='/ai-subtitles/'+('a'.repeat(64));
const url='https://stremio.example'+route+'/placeholder/'+('b'.repeat(40))+'/0/'+('c'.repeat(64))+'.srt';
const legacyUrl='https://stremio.example'+route+'/file/'+('b'.repeat(40))+'/'+('c'.repeat(64))+'.srt';
const oldLocalUrl='http://127.0.0.1:11470'+route+'/placeholder/'+('b'.repeat(40))+'/0/'+('c'.repeat(64))+'.srt';
const externalOriginUrl='https://stremio-other-host.example:12470'+route+'/placeholder/'+('b'.repeat(40))+'/0/'+('c'.repeat(64))+'.srt';
const wrappedUrl='https://stremio.example/proxy/https%3A%2F%2Fexternal.example%2Fai-subtitles%2F'+('a'.repeat(64))+'%2Fplaceholder%2F'+('b'.repeat(40))+'%2F0%2F'+('c'.repeat(64))+'.srt';
const queryWrappedUrl='https://stremio.example/subtitles.vtt?url='+encodeURIComponent(url);
const events={};const timers=[];const calls=[];
let playing=false;
const media={currentTime:5100,readyState:4,paused:false,ended:false,
  pause(){this.paused=true;},play(){this.paused=false;return Promise.resolve();},
  getBoundingClientRect:()=>({left:0,top:0,bottom:400,width:800,height:400})};
const body={appendChild:(el)=>{el.parentNode=body;}};
const document={
  body,hidden:false,querySelector:()=>media,
  createElement:()=>({style:{},setAttribute(){},textContent:'',parentNode:null,children:[],appendChild(node){this.children.push(node);node.parentNode=this;}}),
  addEventListener:(type,fn)=>{(events[type] ||= []).push(fn)}
};
const localStorage={getItem:key=>key==='profile'?
  JSON.stringify({settings:{streamingServerUrl:'https://stremio.example/'}}):null};
const fetch=async (address,opts={})=>{
  const method=opts.method||'GET';
  calls.push([String(address),method]);
  if(address===route+'/demand/start' || String(address).endsWith('/demand/start')) {
    playing=true;
    return {ok:true,json:async()=>({sessionId:'dummy-valid-session'})};
  }
  if(String(address).includes('/client-event')){
    return {ok:true,json:async()=>({received:true})};
  }
  if(String(address).includes('/heartbeat')){
    return {ok:true,json:async()=>({stage:'ready',currentBlockReady:true,
      bufferedUntilSeconds:5160})};
  }
  if(String(address).includes('/subtitles.srt')){
    return {ok:true,text:async()=>('1\n01:25:00,000 --> 01:25:30,000\nLegenda de teste.\n')};
  }
  if(String(address).includes('/stop')){
    return {ok:true,json:async()=>({stopped:true})};
  }
  throw Error('unexpected fetch '+address);
};
const sandbox={window:null,document,localStorage,console,fetch,setInterval:(fn)=>timers.push(fn),
  URL,Date,Number,String,Array,JSON,Math,setTimeout,
  location:{href:'https://stremio.example/',origin:'https://stremio.example'}};
sandbox.window=sandbox;
sandbox.addEventListener=(type,fn)=>{(events[type] ||= []).push(fn)};
vm.createContext(sandbox);
vm.runInContext(script,sandbox);
function dispatch(name,detail){(events[name]||[]).forEach(fn=>fn({detail}));}
async function settle(){for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve))}
async function main(){
  for(const fn of timers)fn();await settle();
  if(calls.length)throw Error('work started without selection');
  dispatch('stremiosrv:subtitle-selection',{track:{id:'stremiosrv-ai-choose-source',url:'data:text/plain,'}});
  await settle();
  for(const fn of timers)fn();await settle();
  if(calls.length)throw Error('AI language picker started GPU work before choosing a variant');
  dispatch('stremiosrv:playback-clock',{time:5100,paused:false,buffering:false});
  dispatch('stremiosrv:subtitle-selection',{track:{url}});
  await settle();
  if(!calls.some(x=>x[0].includes('/demand/start')))throw Error('selection did not start');
  if(!media.paused)throw Error('player was not buffered on AI selection');
  for(const fn of timers)fn();await settle();
  if(media.paused)throw Error('player was not resumed after buffer became ready');
  dispatch('stremiosrv:subtitle-selection',{track:null});
  await settle();
  if(!calls.some(x=>x[0].includes('/stop')))throw Error('deselection did not stop');
  document.hidden = true;
  dispatch('visibilitychange',{});
  await settle();
  if(calls.filter(x=>x[0].includes('/stop')).length !== 1)
    throw Error('switching windows wrongly cancelled an active playback');
  document.hidden = false;
  dispatch('visibilitychange',{});
  await settle();
  dispatch('stremiosrv:subtitle-selection',{track:{url:legacyUrl}});
  await settle();
  if(calls.filter(x=>x[0].includes('/demand/start')).length !== 2)throw Error('legacy SRT selection was ignored');
  dispatch('stremiosrv:subtitle-selection',{track:null});
  await settle();
  dispatch('stremiosrv:subtitle-selection',{track:{url:oldLocalUrl}});
  await settle();
  if(calls.filter(x=>x[0].includes('/demand/start')).length !== 3)
    throw Error('old loopback addon URL did not start');
  if(calls.some(x=>x[0].startsWith('http://127.0.0.1')))
    throw Error('client attempted to call its own loopback');
  dispatch('stremiosrv:subtitle-selection',{track:null});
  await settle();
  dispatch('stremiosrv:subtitle-selection',{track:{url:externalOriginUrl}});
  await settle();
  if(calls.filter(x=>x[0].includes('/demand/start')).length !== 4)
    throw Error('valid addon URL on another origin did not start');
  if(calls.some(x=>x[0].startsWith('https://stremio-other-host.example')))
    throw Error('client sent addon token to a foreign origin');
  dispatch('stremiosrv:subtitle-selection',{track:null});
  await settle();
  dispatch('stremiosrv:subtitle-selection',{track:{url:wrappedUrl}});
  await settle();
  if(calls.filter(x=>x[0].includes('/demand/start')).length !== 5)
    throw Error('transport-wrapped addon URL did not start');
  dispatch('stremiosrv:subtitle-selection',{track:null});
  await settle();
  dispatch('stremiosrv:subtitle-selection',{track:{url:queryWrappedUrl}});
  await settle();
  if(calls.filter(x=>x[0].includes('/demand/start')).length !== 6)
    throw Error('query wrapped addon URL did not start');
  dispatch('stremiosrv:subtitle-selection',{track:null});
  await settle();
  process.stdout.write(JSON.stringify({ok:true,starts:calls.filter(x=>x[0].includes('/demand/start')).length,
    stops:calls.filter(x=>x[0].includes('/stop')).length}));
}
main().catch(e=>{console.error(e);process.exitCode=1});

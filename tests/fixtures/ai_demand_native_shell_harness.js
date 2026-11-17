'use strict';
// Native MPV has no HTML <video>, but the Stremio UI forwards its clock.
const fs=require('fs'), vm=require('vm');
const script=fs.readFileSync(process.argv[2],'utf8');
const events={},timers=[],calls=[],overlays=[];
const route='/ai-subtitles/'+('a'.repeat(64));
const track={id:'ai-demand-'+('c'.repeat(64)),origin:'AI Subtitle Generator',url:'https://qa.local'+route+'/placeholder/'+('b'.repeat(40))+'/0/'+('c'.repeat(64))+'.srt'};
let sessionNumber=0;
const body={appendChild:node=>{node.parentNode=body;overlays.push(node)}};
const doc={body,hidden:false,documentElement:{clientWidth:1280,clientHeight:720},
 querySelector:()=>null,
 createElement:()=>({style:{},setAttribute(){},textContent:'',parentNode:null,children:[],appendChild(node){this.children.push(node);node.parentNode=this;}}),
 addEventListener:(key,fn)=>{(events[key] ||= []).push(fn)}};
const fetch=async(url,opts={})=>{
 calls.push([String(url),opts.method||'GET',opts.body]);
 if(String(url).endsWith('/client-event'))return {ok:true,json:async()=>({received:true})};
 if(String(url).endsWith('/demand/start'))return {ok:true,json:async()=>({sessionId:'native-session-'+(++sessionNumber)})};
 if(String(url).includes('/heartbeat'))return {ok:true,json:async()=>({stage:'ready',currentBlockReady:true,bufferedUntilSeconds:480})};
 if(String(url).includes('/subtitles.srt'))return {ok:true,text:async()=>('1\n00:02:01,000 --> 00:02:06,000\nLegendagem AI nativa.\n')};
 if(String(url).endsWith('/stop'))return {ok:true,json:async()=>({stopped:true})};
 throw Error('unexpected request '+url);
};
const sandbox={window:null,document:doc,console,fetch,URL,Date,Number,String,Array,JSON,Math,setTimeout,clearTimeout,
 setInterval:fn=>timers.push(fn),location:{href:'https://qa.local/',origin:'https://qa.local'},innerWidth:1280,innerHeight:720};
sandbox.window=sandbox;sandbox.addEventListener=(key,fn)=>{(events[key] ||= []).push(fn)};
vm.createContext(sandbox);vm.runInContext(script,sandbox);
function event(key,detail){(events[key]||[]).forEach(fn=>fn({detail}));}
async function settle(){for(let i=0;i<9;i++)await new Promise(r=>setImmediate(r));}
async function drive(){for(const fn of timers)fn();await settle();}
function count(route){return calls.filter(x=>x[0].includes(route)).length;}
async function main(){
 await drive();if(calls.length)throw Error('started without selection');
 event('stremiosrv:subtitle-selection',{track});await settle();
 if(count('/demand/start'))throw Error('started without a native playback clock');
 event('stremiosrv:playback-clock',{time:120,paused:false,buffering:false});await settle();
 if(count('/demand/start')!==1)throw Error('native selection failed to start Whisper');
 await drive();if(count('/heartbeat')<1)throw Error('no native playhead heartbeat');
 const last=calls.find(x=>x[0].includes('/heartbeat'));
 if(Math.abs(JSON.parse(last[2]).positionSeconds-120)>3)throw Error('wrong native playhead');
 event('stremiosrv:playback-clock',{time:123,paused:false,buffering:false});await settle();
 await drive();
 if(!overlays.some(x=>x.children.some(y=>y.textContent==='Legendagem AI nativa.')&&x.style.display==='block'))throw Error('native subtitle overlay not visible');
 // Partial-torrent buffering: never show an earlier SRT cue until the
 // native decoder reports an advancing clock after its resume event.
 event('stremiosrv:playback-clock',{time:123.2,paused:false,buffering:true});await settle();
 if(!overlays.some(x=>x.style.display==='none'))throw Error('AI cue displayed during MPV buffering');
 const nativeOverlay=overlays[0];
 if(nativeOverlay.style.display!=='none')throw Error('stale subtitle during buffering');
 event('stremiosrv:playback-clock',{time:123.2,paused:false,buffering:false});await settle();
 if(nativeOverlay.style.display!=='none')throw Error('stale subtitle displayed on first resume tick');
 event('stremiosrv:playback-clock',{time:123.8,paused:false,buffering:false});await settle();
 if(nativeOverlay.style.display!=='block')throw Error('subtitle did not recover after advancing clock');
 event('stremiosrv:ai-subtitle-settings',{sizePercent:150,verticalPercent:5,delaySeconds:0});await drive();
 const overlay=overlays.find(x=>x.children.some(y=>y.textContent==='Legendagem AI nativa.'));
 const cue=overlay.children[overlay.children.length-1];
 if(cue.style.display!=='inline-block'||cue.style.whiteSpace!=='pre-wrap')throw Error('Native cue geometry differs');
 event('stremiosrv:ai-subtitle-settings',{
    textColor:'rgb(255, 255, 0)',backgroundColor:'rgb(0, 0, 0)',
    outlineColor:'rgb(0, 0, 0)',opacityPercent:80});
 await drive();
 const coloured=overlay.children[overlay.children.length-1];
 if(coloured.style.color!=='rgb(255, 255, 0)'||
    coloured.style.backgroundColor!=='rgb(0, 0, 0)'||
    coloured.style.opacity!=='0.8'||
    !coloured.style.textShadow.includes('rgb(0, 0, 0)'))
    throw Error('native colours/opacity did not reach AI cue');
 if(!overlay||overlay.children[overlay.children.length-1].style.fontSize!=='6vmin'||overlay.style.bottom!=='36px'||overlay.style.top!=='auto')
    throw Error('native Size/Vertical Position settings were not applied: '+JSON.stringify(overlay&&overlay.style));

 event('stremiosrv:playback-clock',{time:240,paused:false,buffering:false});await settle();
 await drive();if(count('/heartbeat')<2)throw Error('seek did not trigger heartbeat');
 event('stremiosrv:playback-clock',{time:241,paused:true,buffering:false});await settle();
 if(count('/stop')!==1)throw Error('pause did not terminate active session');
 event('stremiosrv:playback-clock',{time:242,paused:false,buffering:false});await settle();
 if(count('/demand/start')!==2)throw Error('resume did not establish a new session');
 event('stremiosrv:subtitle-selection',{track:null});await settle();
 if(count('/stop')!==2)throw Error('OFF did not stop the selected session');
 await drive();if(count('/demand/start')!==2)throw Error('OFF restarted Whisper');
 console.log(JSON.stringify({ok:true,starts:count('/demand/start'),stops:count('/stop'),heartbeats:count('/heartbeat'),nativeOverlay:true}));
}
main().catch(e=>{console.error(e);process.exitCode=1});

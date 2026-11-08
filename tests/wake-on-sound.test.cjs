// Deterministic offline execution of the complete installed renderer module.
// No microphone, browser, gateway or hosted provider is contacted.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(process.env.TALK_SOURCE || path.join(__dirname,'../desktop/plugin.js'),'utf8');
const drain = async () => { for(let i=0;i<30;i++) await Promise.resolve(); };
function harness(prefs={}, locale='en') {
  let now=10000, id=0, level=128, deferMic=false, rejectSession=false;
  const timers=new Map(), streams=[], contexts=[], peers=[], requests=[], pending=[], components=[];
  class Events {
    constructor(){this.listeners=new Map();}
    addEventListener(n,f){if(!this.listeners.has(n))this.listeners.set(n,new Set());this.listeners.get(n).add(f);}
    removeEventListener(n,f){this.listeners.get(n)?.delete(f);}
    dispatchEvent(e){for(const f of this.listeners.get(e.type)||[])f(e);}
    count(){return [...this.listeners.values()].reduce((n,s)=>n+s.size,0);}
  }
  const window=new Events(), document=new Events(), hostEvents=new Events();
  document.visibilityState='visible'; document.body={appendChild(){}};
  const timer=(fn,ms,repeat)=>{timers.set(++id,{fn,at:now+(ms||1),ms:ms||1,repeat});return id;};
  function stream(){const t={kind:'audio',enabled:true,stopped:false,stop(){this.stopped=true;}};const s={getTracks:()=>[t],getAudioTracks:()=>[t]};streams.push(s);return s;}
  const node=()=>({gain:{value:0,setValueAtTime(){},linearRampToValueAtTime(){},exponentialRampToValueAtTime(){}},frequency:{value:0},connect(){},disconnect(){},start(){},stop(){}});
  class AC {
    constructor(){this.state='running';this.sampleRate=48000;this.currentTime=0;this.destination={};contexts.push(this);}
    createMediaStreamSource(){return node();}
    createGain(){return node();} createOscillator(){return node();}
    createAnalyser(){return {...node(),fftSize:512,frequencyBinCount:256,getByteTimeDomainData(b){b.fill(level);},getByteFrequencyData(b){b.fill(0);}};}
    async resume(){this.state='running';} async close(){this.state='closed';}
  }
  class PC {
    constructor(){this.connectionState='new';this.senders=[];peers.push(this);}
    addTrack(t){this.senders.push({track:t});} getSenders(){return this.senders;}
    createDataChannel(){this.dc={readyState:'open',frames:[],send(t){this.frames.push(JSON.parse(t));},close(){this.readyState='closed';}};return this.dc;}
    async createOffer(){return {sdp:'synthetic-offer'};} async setLocalDescription(){}
    async setRemoteDescription(){this.connectionState='connected';this.onconnectionstatechange?.();}
    async getStats(){return new Map();} close(){this.connectionState='closed';}
  }
  const values=new Map(Object.entries(prefs)), ctx={storage:{get:k=>values.get(k),set:(k,v)=>values.set(k,v)},
    onDispose(fn){this.dispose=fn;},register(){},async rest(route,opts){requests.push({route,opts});if(route==='/codexlive/session'){if(rejectSession)throw Error('synthetic connection error');return {answer:'synthetic-answer',threadId:'synthetic-thread'};}return {bots:[]};}};
  let rendering=null;
  function mount(name){
    const c={hooks:[],effects:[],index:0,tree:null,cleanups:[],render(){this.index=0;rendering=this;this.tree=vm.runInContext(`${name}({ctx})`,s);rendering=null;const e=this.effects.splice(0);for(const [i,f]of e){this.cleanups[i]?.();this.cleanups[i]=f();}return this.tree;},unmount(){for(const f of this.cleanups)f?.();this.cleanups=[];}};
    components.push(c);c.render();return c;
  }
  const useState=init=>{const c=rendering,i=c.index++;if(!(i in c.hooks))c.hooks[i]=typeof init==='function'?init():init;return [c.hooks[i],v=>{c.hooks[i]=typeof v==='function'?v(c.hooks[i]):v;}];};
  const useRef=init=>{const c=rendering,i=c.index++;return c.hooks[i]||(c.hooks[i]={current:init});};
  const useEffect=(fn,deps)=>{const c=rendering,i=c.index++,prev=c.hooks[i];if(!prev||!deps||deps.some((d,j)=>d!==prev[j])){c.hooks[i]=deps;c.effects.push([i,fn]);}};
  const host={state:{focusedSessionProfile:{get:()=>''},focusedSessionId:{get:()=>'synthetic-chat'}},
    request:async(method,params)=>{requests.push({route:method,opts:params});return {};},
    onEvent:(type,fn)=>{hostEvents.addEventListener(type,fn);return ()=>hostEvents.removeEventListener(type,fn);}};
  const sdk=new Proxy({host,useValue:a=>a?.get?.(),Switch:'Switch'}, {get:(o,k)=>k in o?o[k]:String(k)});
  const s=vm.createContext({console,window,document,ctx,sdk,useState,useRef,useEffect,jsx:(type,props)=>({type,props}),jsxs:(type,props)=>({type,props}),
    navigator:{language:locale,onLine:true,mediaDevices:{getUserMedia:()=>deferMic?new Promise(resolve=>pending.push(resolve)):Promise.resolve(stream()),enumerateDevices:async()=>[]}},
    AudioContext:AC,RTCPeerConnection:PC,Uint8Array,CustomEvent:class{constructor(type,p){this.type=type;Object.assign(this,p);}},
    performance:{now:()=>now},Date:class extends Date{static now(){return now;}},
    setTimeout:(f,m)=>timer(f,m,false),setInterval:(f,m)=>timer(f,m,true),clearTimeout:i=>timers.delete(i),clearInterval:i=>timers.delete(i),
    requestAnimationFrame:f=>timer(f,42,false),cancelAnimationFrame:i=>timers.delete(i)});
  window.AudioContext=AC;
  vm.runInContext(source.replace(/^import .*$/gm,'').replace('export default {','const plugin = {'),s);
  const run=code=>vm.runInContext(code,s);
  async function advance(ms){const end=now+ms;let count=0;while(true){let next;for(const [i,t]of timers)if(t.at<=end&&(!next||t.at<next[1].at))next=[i,t];if(!next)break;if(++count>200000)throw Error('timer runaway');const [i,t]=next;now=t.at;if(t.repeat)t.at+=t.ms;else timers.delete(i);t.fn();await drain();}now=end;await drain();}
  function find(tree,predicate){if(!tree)return null;if(Array.isArray(tree)){for(const x of tree){const r=find(x,predicate);if(r)return r;}return null;}if(typeof tree!=='object')return null;return predicate(tree)?tree:find(tree.props?.children,predicate);}
  const start=()=>run("startLive(ctx,{engine:'codex',voice:'cove',log:()=>{}})");
  return {run,mount,find,start,ctx,window,document,hostEvents,timers,streams,contexts,peers,requests,pending,advance,
    speech:()=>{level=150;},defer:()=>{deferMic=true;},resolve:()=>{deferMic=false;pending.shift()(stream());},fail:()=>{rejectSession=true;},
    openTracks:()=>streams.flatMap(s=>s.getTracks()).filter(t=>!t.stopped).length,
    openContexts:()=>contexts.filter(c=>c.state!=='closed').length};
}
function enable(h,on=true){h.run(`setWakeOnSound(ctx,${on})`);}

test('legacy speech setting never enables mic; only exact new opt-in does',async()=>{
  for(const value of [undefined,'0','true',1]){
    const h=harness({'wakeOnSpeech.v1':'1','wakeOnSound.v1':value});const c=h.mount('ComposerLiveButton');await drain();
    assert.equal(h.openTracks(),0);assert.equal(h.run('wakeOnSoundEnabled(ctx)'),false);c.unmount();
  }
  const h=harness({'wakeOnSound.v1':'1'});const c=h.mount('ComposerLiveButton');await drain();assert.equal(h.openTracks(),1);c.unmount();
});
test('localized settings and off-call indicator preserve upstream voice groups',async()=>{
  for(const [locale,label,indicator]of [['en','Wake on sound','Local listening'],['es','Activar con sonido','Escucha local'],['de','Bei Geräuschen aktivieren','Lokales Mithören']]){
    const h=harness({},locale), c=h.mount('ComposerLiveButton'), f=h.mount('ConfigForm');await drain();
    const sw=h.find(f.tree,n=>n.type==='Switch'&&n.props['aria-label']===label);assert.ok(sw);sw.props.onCheckedChange(true);await drain();c.render();
    assert.ok(h.find(c.tree,n=>n.props?.children===indicator));assert.equal(h.peers.length,0);
    assert.equal(h.run("voiceGroupsFor('codex').flatMap(g=>g.voices).some(v=>v.id==='rindo')"),true);
    sw.props.onCheckedChange(false);c.render();assert.equal(h.find(c.tree,n=>n.props?.children===indicator),null);c.unmount();f.unmount();
  }
});
test('sound-triggered failed call never retries; current voice reaches normal session',async()=>{
  const h=harness({'engine.v1':'codex','voice.v3':'rindo'});h.mount('ComposerLiveButton');h.fail();enable(h);await drain();h.speech();await h.advance(350);
  assert.equal(h.peers.length,1);assert.equal(h.openTracks(),0);assert.equal(h.run('bus.stage'),'error');
  assert.equal(h.requests.find(r=>r.route==='/codexlive/session').opts.body.voice,'rindo');
  await h.advance(5000);assert.equal(h.peers.length,1);assert.equal(h.openTracks(),0);
});


test('default OFF, legacy timer ignored, ordinary manual hangup releases mic',async()=>{
  const h=harness({'idle.v1':'30'});const c=h.mount('ComposerLiveButton');await drain();
  assert.equal(h.run('wakeOnSoundEnabled(ctx)'),false);assert.equal(h.openTracks(),0);
  const call=await h.start();call.close();await drain();assert.equal(h.openTracks(),0);
  await h.advance(2000);assert.equal(h.run('bus.stage'),'idle');c.unmount();
});
test('settings switch is default OFF and immediately stops local monitoring',async()=>{
  const h=harness();h.mount('ComposerLiveButton');const c=h.mount('ConfigForm');await drain();
  const control=h.find(c.tree,n=>n.type==='Switch'&&n.props['aria-label']==='Wake on sound');
  assert.ok(control,'visible wake switch');assert.equal(control.props.checked,false);
  control.props.onCheckedChange(true);await drain();assert.equal(h.openTracks(),1);
  c.render();h.find(c.tree,n=>n.type==='Switch'&&n.props['aria-label']==='Wake on sound').props.onCheckedChange(false);
  assert.equal(h.openTracks(),0);assert.equal(h.openContexts(),0);
});
test('off -> local probe -> sound starts exactly one ordinary call and releases probe',async()=>{
  const h=harness();h.mount('ComposerLiveButton');enable(h);await drain();
  assert.equal(h.openTracks(),1);assert.equal(h.peers.length,0);assert.equal(h.requests.filter(r=>r.route.includes('/session')).length,0);
  const probe=h.streams[0];h.speech();await h.advance(350);
  assert.equal(probe.getTracks()[0].stopped,true);assert.equal(h.peers.length,1);assert.equal(h.openTracks(),1);
  await h.advance(1500);assert.equal(h.peers.length,1);assert.equal(h.requests.filter(r=>r.route==='/codexlive/session').length,1);
  enable(h,false);assert.equal(h.openTracks(),1,'disabling wake does not mute an active manual call');
  h.window.__talkLiveHandle.close();assert.equal(h.openTracks(),0);
});
test('manual hangup arms only when enabled; connection failure never arms',async()=>{
  const h=harness();h.mount('ComposerLiveButton');enable(h);await drain();const call=await h.start();
  call.close();await drain();assert.equal(h.run('bus.stage'),'listening');assert.equal(h.openTracks(),1);
  const next=await h.start();h.peers.at(-1).connectionState='failed';h.peers.at(-1).onconnectionstatechange();await drain();
  assert.equal(h.run('bus.stage'),'error');assert.equal(h.openTracks(),0);await h.advance(1000);assert.equal(h.openContexts(),0);
  next.close();await drain();assert.equal(h.openTracks(),0,'stale closed handles cannot rearm');
});
test('disable while getUserMedia pending stops late tracks without creating context',async()=>{
  const h=harness();h.mount('ComposerLiveButton');h.defer();enable(h);enable(h,false);h.resolve();await drain();
  assert.equal(h.openTracks(),0);assert.equal(h.openContexts(),0);assert.equal(h.peers.length,0);
});
test('rapid disable/re-enable cannot let old mic promise replace new probe',async()=>{
  const h=harness();h.mount('ComposerLiveButton');h.defer();enable(h);enable(h,false);enable(h);assert.equal(h.pending.length,2);
  h.resolve();await drain();assert.equal(h.openTracks(),0);h.resolve();await drain();assert.equal(h.openTracks(),1);
  enable(h,false);assert.equal(h.openTracks(),0);assert.equal(h.openContexts(),0);
});
test('active silent call and active task never time out, including legacy idle preference',async()=>{
  const h=harness({'idle.v1':'30'});h.mount('ComposerLiveButton');const call=await h.start();
  await h.advance(125000);assert.equal(h.run('bus.live'),true);assert.equal(h.peers[0].connectionState,'connected');
  h.run('_delegBusy=true');await h.advance(125000);assert.equal(h.run('bus.live'),true);call.close();
});
test('completed task never reconnects or injects into a later call; live delegation still responds',async()=>{
  const h=harness();h.mount('ComposerLiveButton');const call=await h.start();
  h.run("_delegRespond(_liveRefs.pc.dc,'item','live result')");assert.equal(h.peers[0].dc.frames[0].type,'delegation.context.append');
  call.close();h.run("_delegRespond({readyState:'closed'},'item','finished off call')");await h.advance(5000);assert.equal(h.peers.length,1);
  await h.start();h.run("handleRealtimeEvent({type:'session.started'},_liveRefs.pc.dc,ctx)");assert.equal(h.peers[1].dc.frames.length,0);
});
test('unmount cleans probe, pending acquisition, global handles and document listeners',async()=>{
  const h=harness();const c=h.mount('ComposerLiveButton');enable(h);await drain();c.unmount();await drain();
  assert.equal(h.openTracks(),0);assert.equal(h.openContexts(),0);assert.equal(h.window.count(),0);assert.equal(h.document.count(),0);assert.equal(h.timers.size,0);
  assert.equal(h.window.__talkLiveHandle,undefined);
  const x=harness();const d=x.mount('ComposerLiveButton');x.defer();enable(x);d.unmount();x.resolve();await drain();assert.equal(x.openTracks(),0);assert.equal(x.openContexts(),0);
});
test('plugin disposal cleans active call and prevents late start from acquiring resources',async()=>{
  const h=harness();const c=h.mount('ComposerLiveButton');h.run('plugin.register(ctx)');await h.start();h.ctx.dispose();c.unmount();
  assert.equal(h.openTracks(),0);assert.equal(h.openContexts(),0);assert.equal(h.peers[0].connectionState,'closed');assert.equal(h.document.count(),0);
  const x=harness();x.mount('ComposerLiveButton');x.run('plugin.register(ctx)');x.defer();const pending=x.start();x.ctx.dispose();x.resolve();await assert.rejects(pending,/cancel/i);assert.equal(x.openTracks(),0);assert.equal(x.peers.length,0);
});
test('session negotiation failure releases acquired mic, context, timers and peer',async()=>{
  const h=harness();h.mount('ComposerLiveButton');h.fail();await assert.rejects(h.start(),/synthetic/);await h.advance(1000);
  assert.equal(h.openTracks(),0);assert.equal(h.openContexts(),0);assert.equal(h.peers[0].connectionState,'closed');
  assert.equal(h.run('bus.live'),false);
});
test('repeated enable and multiple composer owners share one probe and one call',async()=>{
  const h=harness();const a=h.mount('ComposerLiveButton'),b=h.mount('ComposerLiveButton');
  enable(h);enable(h);await drain();assert.equal(h.streams.length,1);a.unmount();assert.equal(h.openTracks(),1);
  h.speech();await h.advance(350);assert.equal(h.peers.length,1);b.unmount();
  assert.equal(h.openTracks(),0);assert.equal(h.openContexts(),0);assert.equal(h.window.count(),0);assert.equal(h.document.count(),0);
});
test('pending manual start supersedes pending probe; repeated start cannot open another mic',async()=>{
  const h=harness();h.mount('ComposerLiveButton');h.defer();enable(h);const call=h.start();
  await assert.rejects(h.start(),/already starting/);assert.equal(h.pending.length,2);
  h.resolve();await drain();assert.equal(h.openTracks(),0);h.resolve();await call;
  assert.equal(h.peers.length,1);assert.equal(h.openTracks(),1);
});
test('mic permission rejection reports local error without retries or hosted requests',async()=>{
  const h=harness();h.mount('ComposerLiveButton');h.run("navigator.mediaDevices.getUserMedia=async()=>{throw {name:'NotAllowedError'}}");
  enable(h);await drain();await h.advance(5000);assert.equal(h.openTracks(),0);assert.equal(h.peers.length,0);
  assert.match(h.run('bus.err'),/NotAllowedError/);assert.equal(h.run('_wakeProbe'),null);
  assert.equal(h.requests.filter(r=>r.route.includes('/session')).length,0);
});
test('actual delegation completion speaks while live, never reconnects after hangup',async()=>{
  const h=harness();h.mount('ComposerLiveButton');const call=await h.start();
  h.run("handleDelegation(ctx,{item:{id:'d1',content:[{text:'Please inspect the local report and summarize the findings carefully.'}]}},_liveRefs.pc.dc)");
  await drain();assert.equal(h.hostEvents.count(),3);assert.equal(h.requests.filter(r=>r.route==='prompt.submit').length,1);
  h.hostEvents.dispatchEvent({type:'message.complete',session_id:'synthetic-chat',text:'Synthetic live result.'});await h.advance(350);
  assert.equal(h.peers[0].dc.frames.at(-1).type,'delegation.context.append');assert.equal(h.hostEvents.count(),0);
  h.run("handleDelegation(ctx,{item:{id:'d2',content:[{text:'Please inspect another distinct report and provide the relevant details.'}]}},_liveRefs.pc.dc)");
  await drain();call.close();h.hostEvents.dispatchEvent({type:'message.complete',session_id:'synthetic-chat',text:'Synthetic off-call result.'});
  await h.advance(5000);assert.equal(h.peers.length,1);assert.equal(h.hostEvents.count(),0);assert.equal(h.openTracks(),0);
});
test('disposal cancels delegation observers and wait timer, not the submitted chat task',async()=>{
  const h=harness();const c=h.mount('ComposerLiveButton');h.run('plugin.register(ctx)');await h.start();
  h.run("handleDelegation(ctx,{item:{id:'d1',content:[{text:'Please inspect the report and explain the detailed findings in the chat.'}]}},_liveRefs.pc.dc)");
  await drain();assert.equal(h.hostEvents.count(),3);h.ctx.dispose();c.unmount();await drain();
  assert.equal(h.hostEvents.count(),0);assert.equal(h.timers.size,0);assert.equal(h.openTracks(),0);assert.equal(h.openContexts(),0);
  assert.equal(h.requests.filter(r=>r.route==='prompt.submit').length,1);assert.equal(h.run('_delegBusy'),false);
});
test('disable while AudioContext.resume is pending closes acquired tracks/context',async()=>{
  const h=harness();h.mount('ComposerLiveButton');
  h.run("let resumeAudio; const AC=window.AudioContext; window.AudioContext=class extends AC {constructor(){super();this.state='suspended'} resume(){return new Promise(resolve=>resumeAudio=resolve)}}");
  enable(h);await drain();assert.equal(h.openTracks(),1);assert.equal(h.run('bus.stage'),'listening');enable(h,false);
  assert.equal(h.openTracks(),0);assert.equal(h.openContexts(),0);h.run('resumeAudio()');await drain();
  h.speech();await h.advance(1000);assert.equal(h.peers.length,0);assert.equal(h.run('_wakeProbe'),null);
});
test('disposal during session negotiation stops late backend thread and cannot create a handle',async()=>{
  const h=harness();const c=h.mount('ComposerLiveButton');h.run('plugin.register(ctx)');
  h.run("let resolveSession; const rest=ctx.rest.bind(ctx); ctx.rest=(route,opts)=>route==='/codexlive/session'?new Promise(resolve=>resolveSession=resolve):rest(route,opts)");
  const pending=h.start();await drain();assert.equal(h.peers.length,1);h.ctx.dispose();c.unmount();
  assert.equal(h.openTracks(),0);assert.equal(h.openContexts(),0);h.run("resolveSession({answer:'late-answer',threadId:'late-thread'})");
  await assert.rejects(pending,/cancel/i);assert.equal(h.peers[0].connectionState,'closed');
  assert.equal(h.requests.filter(r=>r.route==='/codexlive/stop'&&r.opts.body.threadId==='late-thread').length,1);
  assert.equal(h.window.__talkLiveHandle,undefined);
});

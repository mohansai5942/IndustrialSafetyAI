const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/js/camera.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
class Element extends EventTarget {
 constructor() { super(); this.dataset={}; this.disabled=true; this.textContent=''; this.className=''; this.hidden=false; this.srcObject=null; this.videoWidth=0; }
 async play() { this.videoWidth=640; this.dispatchEvent(new Event('playing')); }
 click() { if (!this.disabled) this.dispatchEvent(new Event('click')); }
}
class Track extends EventTarget { constructor(){super();this.kind='video';this.readyState='live';} stop(){this.readyState='ended';} }
class Stream { constructor(tracks=[new Track()]){this.tracks=tracks;} getTracks(){return this.tracks;} getVideoTracks(){return this.tracks;} }
function setup(options={}) {
 const ids=['startCamera','stopCamera','localVideo','processedVideo','cameraStatus','camera','previewLabel'];
 const elements=Object.fromEntries(ids.map(id=>[id,new Element()]));
 const document=new EventTarget();document.readyState=options.loading?'loading':'complete';document.getElementById=id=>elements[id];
 const window=new EventTarget();window.isSecureContext=true;
 const requests=[], logs=[];let cameraCalls=0;
 const stream=new Stream();
 class Peer extends EventTarget {
  constructor(config){super();this.config=config;this.connectionState='new';this.iceGatheringState='complete';}
  addTrack(track){this.sentTrack=track;}
  async createOffer(){return {type:'offer',sdp:'v=0\r\n'};}
  async setLocalDescription(offer){this.localDescription=offer;}
  async setRemoteDescription(answer){
   assert.equal(answer.type,'answer');
   this.connectionState='connected';this.dispatchEvent(new Event('connectionstatechange'));
   const event=new Event('track');event.track=new Track();event.streams=[new Stream([event.track])];this.dispatchEvent(event);
  }
  close(){this.connectionState='closed';this.dispatchEvent(new Event('connectionstatechange'));}
 }
 window.RTCPeerConnection=Peer;
 const fetch=async(url,opts={})=>{
  requests.push({url,opts});
  if(options.fetch)return options.fetch(url,opts);
  return {ok:true,status:200,json:async()=>url.endsWith('/config')?{iceServers:[]}:{type:'answer',sdp:'v=0\r\n',token:'test-token'}};
 };
 const context={document,window,navigator:{mediaDevices:{getUserMedia:()=>{cameraCalls++;return options.permission?options.permission():Promise.resolve(stream);}}},
  RTCPeerConnection:Peer,MediaStream:Stream,AbortController,fetch,setTimeout,clearTimeout,
  console:{info:(...x)=>logs.push(x.join(' ')),warn:()=>{},error:()=>{}}};
 vm.runInNewContext(source,context);
 return {elements,document,window,stream,requests,logs,get calls(){return cameraCalls;}};
}
test('bind after DOM ready, click requests permission before network, receive video, stop and reconnect',async()=>{
 const h=setup({loading:true});assert.equal(h.elements.startCamera.disabled,true);
 h.document.dispatchEvent(new Event('DOMContentLoaded'));
 assert.equal(h.elements.startCamera.disabled,false);
 h.elements.startCamera.click();
 assert.equal(h.calls,1);assert.equal(h.requests.length,0);
 await tick();
 assert.equal(h.elements.camera.textContent,'ONLINE');
 assert.equal(h.elements.processedVideo.hidden,false);
 assert.equal(h.requests[1].url,'/api/webrtc/offer');
 assert.equal(JSON.parse(h.requests[1].opts.body).type,'offer');
 h.elements.stopCamera.click();await tick();
 assert.equal(h.stream.getTracks()[0].readyState,'ended');
 assert(h.requests.some(x=>x.url==='/api/webrtc/stop'));
 assert.equal(h.elements.camera.textContent,'OFFLINE');
 h.elements.startCamera.click();await tick();assert.equal(h.calls,2);
 h.elements.stopCamera.click();
});
test('denied permission stays offline with visible error and retry enabled',async()=>{
 const h=setup({permission:()=>Promise.reject(Object.assign(new Error('Denied'),{name:'NotAllowedError'}))});
 h.elements.startCamera.click();await tick();
 assert.match(h.elements.cameraStatus.textContent,/permission blocked/);
 assert.equal(h.elements.startCamera.disabled,false);assert.equal(h.requests.length,0);
 h.elements.stopCamera.click();
});
test('signaling error keeps local video visible and does not claim AI online',async()=>{
 const h=setup({fetch:async()=>({ok:false,status:503,json:async()=>({error:'Backend unavailable'})})});
 h.elements.startCamera.click();await tick();
 assert.equal(h.elements.localVideo.hidden,false);
 assert.equal(h.elements.localVideo.srcObject,h.stream);
 assert.equal(h.elements.camera.textContent,'LOCAL ONLY');
 assert.match(h.elements.cameraStatus.textContent,/Backend unavailable/);
 h.elements.stopCamera.click();
});
test('stop while permission is pending releases a late stream',async()=>{
 let grant;const late=new Stream();const h=setup({permission:()=>new Promise(resolve=>{grant=resolve;})});
 h.elements.startCamera.click();h.elements.stopCamera.click();grant(late);await tick();
 assert.equal(late.getTracks()[0].readyState,'ended');assert.equal(h.requests.length,0);
});
test('stop during offer cleans up the token from a late answer',async()=>{
 let answer;
 const h=setup({fetch:async url=>{
  if(url.endsWith('/config'))return {ok:true,json:async()=>({iceServers:[]})};
  if(url.endsWith('/offer'))return new Promise(resolve=>{answer=()=>resolve({ok:true,json:async()=>({type:'answer',sdp:'v=0',token:'late-token'})});});
  return {ok:true,json:async()=>({ok:true})};
 }});
 h.elements.startCamera.click();await tick();h.elements.stopCamera.click();answer();await tick();
 const cleanup=h.requests.find(x=>x.url.endsWith('/stop'));
 assert.equal(JSON.parse(cleanup.opts.body).token,'late-token');assert.equal(h.elements.camera.textContent,'OFFLINE');
});
test('visibility changes do not cancel a permission/connection flow',async()=>{
 const h=setup();h.elements.startCamera.click();await tick();
 h.document.hidden=true;h.document.dispatchEvent(new Event('visibilitychange'));
 assert.equal(h.stream.getTracks()[0].readyState,'live');h.elements.stopCamera.click();
});

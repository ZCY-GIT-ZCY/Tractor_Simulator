'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Run the real UI controller against a minimal DOM and a delayed HTTP transport.
// Responses are deliberately delivered after leaving/joining another session.
function browser(initialToken = null, saved = null) {
 const elements = new Map(), requests = [], listeners = new Map();
 function element(id) {
  if (!elements.has(id)) elements.set(id, {id, hidden:true, disabled:false, value:'0', checked:true,
   dataset:{}, style:{setProperty(){}}, classList:{toggle(){}}, clientWidth:900,
   innerHTML:'', textContent:'', addEventListener(){}, setAttribute(){},
   querySelector(selector){return element(id+selector);}, querySelectorAll(){return [];},
   appendChild(child){child.parentElement=this;}, click(){return this.onclick?.();}, select(){}});
  return elements.get(id);
 }
 function storage(entries) {
  const data = new Map(entries);
  return {getItem:key=>data.get(key)??null, setItem:(key,value)=>data.set(key,String(value)), removeItem:key=>data.delete(key)};
 }
 const sessionStorage = storage(initialToken?[['tractor_session',initialToken]]:[]);
 const localStorage = storage(saved?[['tractor_resume',JSON.stringify(saved)]]:[]);
 const context = vm.createContext({document:{getElementById:element, querySelectorAll(){return [];},
  addEventListener:(event,fn)=>listeners.set(event,fn)}, sessionStorage,localStorage,
  location:{search:'',origin:'http://127.0.0.1:8765',hostname:'127.0.0.1'},
  window:{addEventListener(){}}, navigator:{}, innerWidth:1280,
  URL,URLSearchParams,AbortController, setTimeout(){return 1;},clearTimeout(){},
  TractorHandOrder:{sort:cards=>cards.slice()},TractorDrawPreview:{cardFor:()=>null},
  fetch(url,options){
   if(url==='/api/site')return Promise.resolve({ok:true,json:async()=>({})});
   let finish;
   const promise = new Promise(resolve=>finish=resolve);
   requests.push({url,options,reply(data,status=200){finish({ok:status<400,status,json:async()=>data});}});
   return promise;
  }});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8'),context);
 // Poll transport is covered by the Python HTTP tests; keep these tests focused
 // on the competing action, leave, bootstrap and resume promises.
 vm.runInContext('poll=async()=>{}',context);
 return {context,element,requests,listeners,sessionStorage,localStorage,
  run:code=>vm.runInContext(code,context),get:code=>vm.runInContext(code,context)};
}
function snapshot(room='room_one',token='token_one') {
 return {room,token,mode:'pvp',version:4,seat:0,host:true,controlled:[0],started:true,paused:false,
  config:{initial_level:2,auction:true,banker:0},
  players:[0,1,2,3].map(seat=>({seat,name:'Player '+seat,online:true,occupied:true})),
  state:{round:1,phase:'dealing',current_player:0,level:2,levels:[2,2],trump:null,bid:null,
   hand:[1],hand_counts:[1,0,0,0],trick:[],trick_no:0,last_trick:null,deal_count:1,redeals:0,
   events:[],bid_options:[],kitty:null,kitty_public:false,kitty_owner:null,
   result:null,score:0,captured_score:0,penalty_score:0,bottom_score:0}};
}
function sit(browser,data=snapshot()) {
 browser.context.fixture=data;
 browser.run('token=fixture.token;acceptSnapshot(fixture);rememberSeat()');
}
function latest(browser,path){return browser.requests.findLast(r=>r.url===path);}

test('late action response cannot restore a table after leaving',async()=>{
 const b=browser();sit(b);
 const action=b.element('passButton').onclick();
 const pending=latest(b,'/api/action');
 const leave=b.element('leaveButton').onclick();
 latest(b,'/api/leave').reply({ok:true});await leave;
 pending.reply({...snapshot(),version:5});await action;
 assert.equal(b.get('snap'),null);assert.equal(b.get('token'),null);
 assert.equal(b.element('setupOverlay').hidden,false);assert.equal(b.get('busy'),false);
});
test('late autoplay response cannot restore a table after leaving',async()=>{
 const b=browser();sit(b);
 const autoplay=b.element('autoplayButton').onclick();
 const pending=latest(b,'/api/autoplay');
 const leave=b.element('leaveButton').onclick();
 latest(b,'/api/leave').reply({ok:true});await leave;
 pending.reply({...snapshot(),version:5,autoplay:true});await autoplay;
 assert.equal(b.get('snap'),null);assert.equal(b.element('setupOverlay').hidden,false);
});
test('old bootstrap reply cannot overwrite a newly joined room',async()=>{
 const b=browser('old_token');
 const bootstrap=latest(b,'/api/state');
 const joining=b.element('setupForm').onsubmit({preventDefault(){},target:b.element('setupForm')});
 latest(b,'/api/session').reply(snapshot('new_room','new_token'));await joining;
 bootstrap.reply(snapshot('old_room','old_token'));await new Promise(setImmediate);
 assert.equal(b.get('snap.room'),'new_room');assert.equal(b.get('token'),'new_token');
});
test('newly remembered seat can be resumed after a temporary UI reset',async()=>{
 const b=browser();sit(b);
 b.run('resetUI()');assert.equal(b.element('resumeButton').hidden,false);
 const resume=b.element('resumeButton').onclick();
 const request=latest(b,'/api/state');
 assert.equal(request.options.headers.Authorization,'Bearer token_one');
 request.reply(snapshot());await resume;
 assert.equal(b.get('snap.room'),'room_one');assert.equal(b.sessionStorage.getItem('tractor_session'),'token_one');
});
test('expired bootstrap token is removed instead of offering a broken resume',async()=>{
 const b=browser('expired',{token:'expired',room:'old_room',mode:'pvp'});
 latest(b,'/api/state').reply({code:'SESSION',message:'expired'},403);
 await new Promise(setImmediate);
 assert.equal(b.get('token'),null);assert.equal(b.get('remembered'),null);
 assert.equal(b.localStorage.getItem('tractor_resume'),null);assert.equal(b.element('resumeButton').hidden,true);
});
test('creating a new match releases the old seat first',async()=>{
 const b=browser();sit(b);b.run("snap.state.phase='match_end'");
 const next=b.element('nextRoundButton').onclick();
 const request=latest(b,'/api/leave');assert.ok(request);
 request.reply({ok:true});await next;
 assert.equal(b.get('snap'),null);assert.equal(b.localStorage.getItem('tractor_resume'),null);
});
test('Enter in the rules dialog does not submit selected cards',()=>{
 const b=browser();sit(b);b.element('rulesOverlay').hidden=false;b.element('submitButton').disabled=false;
 b.listeners.get('keydown')({key:'Enter',target:{tagName:'DIV'},preventDefault(){}});
 assert.equal(latest(b,'/api/action'),undefined);
});
test('failed leave retains the seat and clears the pending busy state',async()=>{
 const b=browser();sit(b);b.run('busy=true');
 const leaving=b.element('leaveButton').onclick();
 latest(b,'/api/leave').reply({message:'unavailable'},503);await leaving;
 assert.equal(b.get('token'),'token_one');assert.equal(b.get('busy'),false);
 assert.equal(b.element('leaveButton').disabled,false);
});

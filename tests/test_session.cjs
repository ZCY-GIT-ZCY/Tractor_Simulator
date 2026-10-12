'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Run the real UI controller against a minimal DOM and a delayed HTTP transport.
// Responses are deliberately delivered after leaving/joining another session.
function browser(initialToken = null, saved = null) {
 const elements = new Map(), requests = [], listeners = new Map(), timers = new Map();let timerId=0;
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
  window:{addEventListener:(event,fn)=>listeners.set('window:'+event,fn)}, navigator:{}, innerWidth:1280,
  URL,URLSearchParams,AbortController, setTimeout(fn,delay){const id=++timerId;timers.set(id,{fn,delay});return id;},clearTimeout(id){timers.delete(id);},
  TractorHandOrder:{sort:cards=>cards.slice()},TractorDrawPreview:require('../web/draw-preview.js'),TractorHandLayout:require('../web/hand-layout.js'),
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
 return {context,element,requests,listeners,timers,sessionStorage,localStorage,
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

function ending(){
 const data=snapshot();
 data.state.phase='round_end';data.state.current_player=1;
 data.state.result={round:1,banker:2,winner:0,score:65,captured:60,bottom:10,multiplier:2,
  penalty:-5,next_banker:1,levels_before:[2,2],levels_after:[3,2]};
 data.settlement={banker:2,kitty:[1,2,3,4,5,6,7,8],kitty_points:5,confirmed:false,
  can_confirm:false,ai_banker:false};
 return data;
}

function lobby(){
 const data=snapshot();data.started=false;data.seat=null;data.state=null;data.controlled=[];data.name='Host';
 data.players=data.players.map(p=>({...p,occupied:false,online:false,bot:false}));
 data.members=[{name:'Host',seat:null,self:true,online:true}];data.can_start=false;data.ai_seats=[];
 return data;
}

test('tentative chair is local only; confirm claims it and cancel releases it',async()=>{
 const b=browser(),data=lobby();sit(b,data);const count=b.requests.length;
 b.run('chooseSeat(3)');assert.equal(b.requests.length,count);assert.equal(b.get('snap.seat'),null);
 assert.equal(b.element('confirmSeatButton').disabled,false);
 const confirm=b.element('confirmSeatButton').onclick();const pending=latest(b,'/api/seat/confirm');
 assert.equal(JSON.parse(pending.options.body).seat,3);
 const claimed={...data,version:5,seat:3,controlled:[3],players:data.players.map(p=>p.seat===3?{...p,occupied:true,online:true,name:'Host'}:p)};
 pending.reply(claimed);await confirm;
 assert.match(b.element('lobbySeats').innerHTML,/Host/);assert.equal(b.element('cancelSeatButton').hidden,false);
 const cancel=b.element('cancelSeatButton').onclick();latest(b,'/api/seat/cancel').reply({...data,version:6});await cancel;
 assert.equal(b.get('snap.seat'),null);assert.equal(b.get('tentativeSeat'),null);
 assert.equal(b.element('confirmSeatButton').disabled,true);
});

test('another confirmed occupant clears local preview without submitting it',()=>{
 const b=browser(),data=lobby();sit(b,data);b.run('chooseSeat(1)');
 const occupied={...data,version:5,players:data.players.map(p=>p.seat===1?{...p,occupied:true,name:'<Friend>',online:true}:p)};
 b.context.fixture=occupied;b.run('acceptSnapshot(fixture)');
 assert.equal(b.get('tentativeSeat'),null);assert.equal(b.element('confirmSeatButton').disabled,true);
 assert.match(b.element('lobbySeats').innerHTML,/&lt;Friend&gt;/);
 assert.doesNotMatch(b.element('lobbySeats').innerHTML,/<Friend>/);
});

test('host can fill AI chairs and explicitly start after all chairs are ready',async()=>{
 const b=browser(),data=lobby();data.seat=3;data.controlled=[3];
 data.players[3]={...data.players[3],occupied:true,online:true,name:'Host'};sit(b,data);
 const filling=b.element('fillAIButton').onclick();
 const filled={...data,version:5,ai_seats:[0,1,2],can_start:true,
  players:data.players.map(p=>p.seat!==3?{...p,occupied:true,online:true,bot:true,name:'Enoch AI'}:p)};
 latest(b,'/api/room/ai').reply(filled);await filling;
 assert.equal(b.element('startRoomButton').disabled,false);assert.equal(b.element('lobbyOverlay').hidden,false);
 const starting=b.element('startRoomButton').onclick();const begun={...snapshot(),version:6,seat:3,controlled:[3],players:filled.players};
 latest(b,'/api/room/start').reply(begun);await starting;
 assert.equal(b.element('lobbyOverlay').hidden,true);assert.equal(b.get('snap.started'),true);
});

test('unseated member of active room stays in seating UI with no play controls',()=>{
 const b=browser(),data=lobby();data.started=true;sit(b,data);
 assert.equal(b.element('lobbyOverlay').hidden,false);assert.equal(b.get('isMyTurn()'),false);
 assert.equal(b.element('startRoomButton').hidden,true);
 assert.equal(b.element('closeRoomSeats').hidden,true);
 assert.doesNotThrow(()=>b.listeners.get('window:resize')());
 assert.doesNotThrow(()=>b.listeners.get('keydown')({key:'Escape',target:{tagName:'DIV'}}));
 assert.equal(b.element('lobbyOverlay').hidden,false);
});

test('late seat confirmation after leaving cannot restore the room',async()=>{
 const b=browser();sit(b,lobby());b.run('chooseSeat(2)');
 const confirmation=b.element('confirmSeatButton').onclick();const pending=latest(b,'/api/seat/confirm');
 const leave=b.element('leaveButton').onclick();latest(b,'/api/leave').reply({ok:true});await leave;
 pending.reply({...lobby(),seat:2,version:5});await confirmation;
 assert.equal(b.get('snap'),null);assert.equal(b.get('seatBusy'),false);
 assert.equal(b.element('setupOverlay').hidden,false);
});

test('bid cards persist in every preplay phase and disappear when play starts',()=>{
 const b=browser(),data=snapshot();data.state.bid={player:3,strength:2,cards:[13,67]};sit(b,data);
 for(const phase of ['dealing','final_bidding','burying','countering']){
  b.context.phaseFixture=phase;b.run('snap.state.phase=phaseFixture;renderPlays()');
  assert.match(b.element('playsLayer').innerHTML,/play east declaration/);
  assert.match(b.element('playsLayer').innerHTML,/data-card="13"/);
  assert.match(b.element('playsLayer').innerHTML,/data-card="67"/);
 }
 b.run("snap.state.phase='playing';renderPlays()");
 assert.equal(b.element('playsLayer').innerHTML,'');
});

test('new trump bids notify other seats with escaped nickname and do not repeat',()=>{
 const b=browser();sit(b);
 const data=snapshot();data.version=5;data.players[1].name='<牌友>';
 data.state.bid={player:1,cards:[13],strength:1};data.state.trump='H';
 data.state.public_history=[{seq:2,type:'bid',player:1,cards:[13],trump:'H'}];
 b.context.fixture=data;b.run('acceptSnapshot(fixture)');
 assert.equal(b.element('bidNotice').hidden,false);
 assert.match(b.element('bidNotice').innerHTML,/&lt;牌友&gt; 亮主/);
 assert.match(b.element('bidNotice').innerHTML,/红桃/);
 b.element('bidNotice').hidden=true;b.run('acceptSnapshot(fixture)');
 assert.equal(b.element('bidNotice').hidden,true);
 const own={...data,version:6,state:{...data.state,public_history:[...data.state.public_history,
  {seq:3,type:'bid',player:0,cards:[0,54],trump:'S'}]}};
 b.context.fixture=own;b.run('acceptSnapshot(fixture)');
 assert.equal(b.element('bidNotice').hidden,true);
 const fresh=browser();sit(fresh,data);assert.equal(fresh.element('bidNotice').hidden,true);
});

test('counter replaces earlier shown cards and strengthening has its own notice',()=>{
 const b=browser(),data=snapshot();data.state.bid={player:1,cards:[13],strength:1};
 data.state.public_history=[{seq:2,type:'bid',player:1,cards:[13],trump:'H'}];sit(b,data);
 const next={...data,version:5,state:{...data.state,trump:'H',bid:{player:1,cards:[13,67],strength:2},
  public_history:[...data.state.public_history,{seq:3,type:'bid',player:1,cards:[13,67],trump:'H'}]}};
 b.context.fixture=next;b.run('acceptSnapshot(fixture)');assert.match(b.element('bidNotice').innerHTML,/加固/);
 const counter={...next,version:6,state:{...next.state,trump:null,bid:{player:2,cards:[53,107],strength:4},
  public_history:[...next.state.public_history,{seq:4,type:'bid',player:2,cards:[53,107],trump:null}]}};
 b.context.fixture=counter;b.run('acceptSnapshot(fixture)');
 assert.match(b.element('bidNotice').innerHTML,/无主/);
 assert.match(b.element('playsLayer').innerHTML,/play north declaration/);
 assert.doesNotMatch(b.element('playsLayer').innerHTML,/data-card="13"/);
});

test('unconfirmed result shows eight bottom cards and score panel without a modal',()=>{
 const b=browser();sit(b,ending());
 assert.equal(b.element('resultOverlay').hidden,true);
 assert.equal(b.element('settlementPanel').hidden,false);
 assert.equal((b.element('settlementBottom').innerHTML.match(/data-card=/g)||[]).length,8);
 assert.match(b.element('settlementFormula').innerHTML,/5 × 2 = 10/);
 assert.match(b.element('settlementWaiting').textContent,/Player 2/);
 assert.equal(b.element('confirmResultButton').hidden,true);
 b.element('phaseLabel').onclick();assert.equal(b.element('resultOverlay').hidden,true);
 b.context.fixture={...ending(),version:5,settlement:{...ending().settlement,confirmed:true}};
 b.run('acceptSnapshot(fixture)');assert.equal(b.element('resultOverlay').hidden,false);
});

test('banker confirmation response after leaving cannot restore a completed table',async()=>{
 const b=browser(),data=ending();data.settlement.can_confirm=true;sit(b,data);
 const confirming=b.element('confirmResultButton').onclick();
 const pending=latest(b,'/api/confirm-result');assert.ok(pending);
 const leaving=b.element('leaveButton').onclick();latest(b,'/api/leave').reply({ok:true});await leaving;
 pending.reply({...data,version:5,settlement:{...data.settlement,confirmed:true}});await confirming;
 assert.equal(b.get('snap'),null);assert.equal(b.element('resultOverlay').hidden,true);
 assert.equal(b.element('settlementPanel').hidden,true);
});

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
 assert.equal(b.element('roomField').hidden,false);
 assert.match(b.element('setupForm[type=submit]').innerHTML,/进入房间/);
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

test('following with singles is not described as a throw attempt',async()=>{
 const b=browser(),data=snapshot();data.state.phase='playing';
 data.state.hand=[1,2];data.state.trick=[{player:1,cards:[3,57]}];
 data.state.forced_play=[1,2];sit(b,data);
 const validation=b.run('validateSelection()');
 latest(b,'/api/validate').reply({valid:true,kind:'throw',message:'跟 2 张'});await validation;
 assert.match(b.element('validationDetail').textContent,/^唯一合法跟牌已预选 · 点击出牌确认；首家/);
 assert.equal(b.element('submitButton').disabled,false);
});
test('a lead throw keeps its referee-only warning',async()=>{
 const b=browser(),data=snapshot();data.state.phase='playing';data.state.hand=[1,2];sit(b,data);
 const validation=b.run('selected=new Set([1,2]);validateSelection()');
 latest(b,'/api/validate').reply({valid:true,kind:'throw',message:'甩牌尝试'});await validation;
 assert.equal(b.element('validationDetail').textContent,'甩牌尝试，提交后由裁判判定');
});
test('settlement does not say the next banker is still acting',async()=>{
 for(const phase of ['round_end','match_end']){
  const b=browser(),data=snapshot();data.state.phase=phase;data.state.current_player=1;
  b.context.fixture=data;b.run('snap=fixture');await b.run('validateSelection()');
  assert.equal(b.element('validationTitle').textContent,'本局已结束');
  assert.equal(b.element('validationDetail').textContent,'点击阶段标签查看结算');
  assert.match(b.element('submitButton').innerHTML,/查看结算/);
 }
});

test('waiting player can preselect; other turns preserve cards and own turn revalidates',async()=>{
 const b=browser(),data=snapshot();data.state.phase='playing';data.state.current_player=1;
 data.state.hand=[2,3,4];data.state.trick=[{player:1,cards:[1]}];sit(b,data);
 const before=b.requests.length;b.run('toggleCard(2)');
 assert.equal(b.requests.length,before);assert.equal(b.get('selected.has(2)'),true);
 assert.equal(b.get('canSelectHand()'),true);assert.equal(b.element('submitButton').disabled,true);
 assert.match(b.element('validationTitle').textContent,/已预选 1 张/);
 assert.match(b.element('hand').className,/preselecting/);
 const next={...data,version:5,state:{...data.state,current_player:2,trick:[...data.state.trick,{player:2,cards:[14]}]}};
 b.context.fixture=next;b.run('acceptSnapshot(fixture)');assert.equal(b.get('selected.has(2)'),true);
 const own={...next,version:6,state:{...next.state,current_player:0,trick:[...next.state.trick,{player:3,cards:[27]}]}};
 b.context.fixture=own;b.run('acceptSnapshot(fixture)');
 const validate=latest(b,'/api/validate');assert.equal(JSON.parse(validate.options.body).action.cards[0],2);
 assert.equal(b.element('submitButton').disabled,true);
 validate.reply({valid:true,kind:'single',message:'跟 1 张'});await new Promise(setImmediate);
 assert.equal(b.element('submitButton').disabled,false);
 assert.equal(latest(b,'/api/action'),undefined); // Preselection never submits a turn.
});

test('dealing allows offturn preselection and realtime bidding without a draw action',async()=>{
 const b=browser(),data=snapshot();data.state.current_player=3;data.state.hand=[0,54,1];
 data.state.realtime_bidding=true;data.state.deal_count=8;sit(b,data);
 b.run('toggleCard(0)');assert.equal(b.get('selected.has(0)'),true);
 const validation=latest(b,'/api/validate');
 assert.equal(JSON.parse(validation.options.body).round,1);
 validation.reply({valid:true,message:'可以亮主／反主',version:5,level:2,trump:'H',round:1,redeals:0,phase:'dealing'});
 await new Promise(setImmediate);
 assert.equal(b.element('submitButton').disabled,false);
 assert.match(b.element('submitButton').innerHTML,/亮主 \/ 反主/);
 const submit=b.element('submitButton').onclick();const request=latest(b,'/api/bid');
 assert.equal(JSON.parse(request.options.body).action.cards[0],0);
 assert.equal(latest(b,'/api/action'),undefined);
 request.reply({...data,version:6,state:{...data.state,bid:{player:0,strength:1,cards:[0]},trump:'S'}});
 await submit;assert.equal(b.get('selected.size'),0);assert.equal(b.get('snap.state.deal_count'),8);
});

test('dealing previews survive other drawers, added cards and trump changes but not redeals',()=>{
 const b=browser(),data=snapshot();data.state.current_player=3;data.state.hand=[0,1];
 data.state.realtime_bidding=true;sit(b,data);b.run('toggleCard(0)');
 for(const state of [{...data.state,current_player:2,deal_count:9},
                     {...data.state,current_player:0,hand:[0,1,54],deal_count:10},
                     {...data.state,current_player:1,hand:[0,1,54],trump:'H',deal_count:11}]){
  b.context.fixture={...data,version:b.get('snap.version')+1,state};b.run('acceptSnapshot(fixture)');
  assert.equal(b.get('selected.has(0)'),true);
 }
 b.context.fixture={...data,version:10,state:{...data.state,redeals:1}};b.run('acceptSnapshot(fixture)');
 assert.equal(b.get('selected.size'),0);
});

test('later rejected realtime bid shows its reason and preserves preview for correction',async()=>{
 const b=browser(),data=snapshot();data.state.current_player=3;data.state.hand=[0];data.state.realtime_bidding=true;
 sit(b,data);b.run('selected=new Set([0])');const submitting=b.run('act(selectedAction())');
 latest(b,'/api/bid').reply({valid:false,code:'BID_TOO_WEAK',message:'亮主无效：反主须严格高于当前亮主强度'},422);
 await submitting;assert.match(b.element('toast').textContent,/亮主无效/);
 assert.equal(b.get('selected.has(0)'),true);assert.equal(b.get('busy'),false);
});

test('continuing own draw preserves a bid preview into the next drawer turn',async()=>{
 const b=browser(),data=snapshot();data.state.hand=[0,54];data.state.realtime_bidding=true;
 sit(b,data);b.run('selected=new Set([0,54])');const continuing=b.run('act({type:"pass"})');
 latest(b,'/api/action').reply({...data,version:5,state:{...data.state,current_player:3,deal_count:2}});
 await continuing;assert.deepEqual(Array.from(b.get('selected')),[0,54]);
 assert.equal(b.get('snap.state.current_player'),3);assert.equal(b.get('canBidNow()'),true);
});

test('after dealing the same player must wait for final bid polling',()=>{
 const b=browser(),data=snapshot();data.state.phase='final_bidding';data.state.current_player=3;
 data.state.hand=[0,54];data.state.realtime_bidding=true;sit(b,data);
 assert.equal(b.get('canBidNow()'),false);assert.equal(b.get('canSelectHand()'),false);
 assert.equal(b.element('submitButton').disabled,true);
});

test('six-second completed draw window keeps realtime bids and hides all draw controls',async()=>{
 const b=browser(),data=snapshot();data.state.realtime_bidding=true;data.state.current_player=3;
 data.state.hand=[0,54];data.state.deal_count=100;
 data.state.bidding_window={complete:true,remaining_seconds:6,duration_seconds:6};
 b.run('autoDeal=true');sit(b,data);
 assert.equal(b.get('canBidNow()'),true);assert.equal(b.get('autoDeal'),false);
 for(const id of ['passButton','autoDealButton','randomButton'])assert.equal(b.element(id).hidden,true);
 assert.match(b.element('tableStatus').textContent,/摸牌结束.*还可亮主/);
 b.run('toggleCard(0)');latest(b,'/api/validate').reply({valid:true,message:'可以亮主'});
 await new Promise(setImmediate);assert.equal(b.element('submitButton').disabled,false);
 assert.equal(latest(b,'/api/action'),undefined);
});

test('expired grace disables submission and discards a delayed valid preflight',async()=>{
 const b=browser(),data=snapshot();data.state.realtime_bidding=true;data.state.hand=[0];
 data.state.bidding_window={complete:true,remaining_seconds:6,duration_seconds:6};sit(b,data);
 b.run('toggleCard(0)');const pending=latest(b,'/api/validate');
 b.run('biddingEndAt=Date.now()-1;updateBiddingCountdown()');
 assert.equal(b.get('canBidNow()'),false);assert.equal(b.element('submitButton').disabled,true);
 pending.reply({valid:true,message:'可以亮主'});await new Promise(setImmediate);
 assert.equal(b.element('submitButton').disabled,true);
 assert.match(b.element('tableStatus').textContent,/实时亮主结束/);
 assert.equal(latest(b,'/api/bid'),undefined);
});

test('another bid preserves previews and uses remaining grace instead of a fresh six seconds',()=>{
 const b=browser(),data=snapshot();data.state.realtime_bidding=true;data.state.current_player=3;
 data.state.hand=[0,54];data.state.bidding_window={complete:true,remaining_seconds:6,duration_seconds:6};
 sit(b,data);b.run('selected=new Set([0,54])');
 const changed={...data,version:5,state:{...data.state,bid:{player:1,strength:1,cards:[13]},trump:'H',
  bidding_window:{complete:true,remaining_seconds:2,duration_seconds:6}}};
 b.context.fixture=changed;b.run('acceptSnapshot(fixture)');
 assert.deepEqual(Array.from(b.get('selected')),[0,54]);
 assert.ok(b.get('biddingEndAt-Date.now()')<=2000);
 assert.ok(b.get('biddingEndAt-Date.now()')>1500);
});

test('pausing the room pauses the grace display; leaving cancels its callback',async()=>{
 const b=browser(),data=snapshot();data.state.realtime_bidding=true;data.paused=true;
 data.state.bidding_window={complete:true,remaining_seconds:4,duration_seconds:6};sit(b,data);
 assert.equal(b.get('canBidNow()'),false);assert.match(b.element('tableStatus').textContent,/已暂停/);
 const resumed={...data,version:5,paused:false};b.context.fixture=resumed;b.run('acceptSnapshot(fixture)');
 assert.equal(b.get('canBidNow()'),true);const timer=b.timers.get(b.get('biddingTimer')).fn;
 const leave=b.element('leaveButton').onclick();latest(b,'/api/leave').reply({ok:true});await leave;
 const count=b.requests.length;timer();assert.equal(b.requests.length,count);
 assert.equal(b.get('snap'),null);assert.equal(b.get('biddingEndAt'),0);
});

test('old running services retain turn bidding until restarted with realtime support',async()=>{
 const b=browser(),data=snapshot();sit(b,data);b.run('selected=new Set([1])');
 const submitting=b.run('act(selectedAction())');assert.equal(latest(b,'/api/bid'),undefined);
 latest(b,'/api/action').reply({...data,version:5});await submitting;
});

test('illegal preselection stays selected but cannot be submitted when own turn arrives',async()=>{
 const b=browser(),data=snapshot();data.state.phase='playing';data.state.current_player=1;
 data.state.hand=[2,15];data.state.trick=[{player:1,cards:[1]}];sit(b,data);b.run('toggleCard(15)');
 b.context.fixture={...data,version:5,state:{...data.state,current_player:0}};b.run('acceptSnapshot(fixture)');
 latest(b,'/api/validate').reply({valid:false,message:'须跟完同门牌'});await new Promise(setImmediate);
 assert.equal(b.get('selected.has(15)'),true);assert.equal(b.element('submitButton').disabled,true);
 assert.match(b.element('validationDetail').textContent,/须跟完同门/);
});

test('sole legal follow replaces a conflicting preview, while round changes clear it',()=>{
 const b=browser(),data=snapshot();data.state.phase='playing';data.state.current_player=1;
 data.state.hand=[2,3,15];data.state.trick=[{player:1,cards:[1,55]}];sit(b,data);b.run('toggleCard(15)');
 b.context.fixture={...data,version:5,state:{...data.state,current_player:0,forced_play:[2,3]}};b.run('acceptSnapshot(fixture)');
 assert.deepEqual(Array.from(b.get('selected')),[2,3]);
 b.context.fixture={...data,version:6,state:{...data.state,round:2,trick_no:0}};b.run('acceptSnapshot(fixture)');
 assert.equal(b.get('selected.size'),0);
});

test('new level card cancels auto dealing, including card zero and stale timer callbacks',()=>{
 const b=browser(),data=snapshot();data.state.drawn_card=1;sit(b,data);
 b.run('autoDeal=true;scheduleAutoDeal()');const timer=b.timers.get(b.get('autoTimer')).fn;
 const count=b.requests.length;
 b.context.fixture={...data,version:5,state:{...data.state,hand:[1,0],drawn_card:0,deal_count:5}};
 b.run('acceptSnapshot(fixture)');assert.equal(b.get('autoDeal'),false);
 assert.match(b.element('toast').textContent,/摸到级牌/);assert.equal(b.element('autoDealButton').textContent,'自动摸牌');
 timer();assert.equal(b.requests.length,count);assert.equal(latest(b,'/api/action'),undefined);
});

test('new small or big joker pair stops auto dealing even without a legal bid',()=>{
 for(const [first,second] of [[52,106],[53,107]]){
  const b=browser(),data=snapshot();data.state.hand=[1,first];data.state.drawn_card=first;sit(b,data);
  b.run('autoDeal=true');b.context.fixture={...data,version:5,state:{...data.state,hand:[1,first,second],drawn_card:second,deal_count:5}};
  b.run('acceptSnapshot(fixture)');assert.equal(b.get('autoDeal'),false);
  assert.match(b.element('toast').textContent,/凑齐王对/);
 }
});

test('ordinary draws keep auto dealing; repeats, old joker pairs and other players do not interrupt it',()=>{
 const b=browser(),data=snapshot();data.state.hand=[1,52,106];data.state.drawn_card=1;sit(b,data);
 b.run('autoDeal=true');const ordinary={...data,version:5,state:{...data.state,hand:[1,52,106,3],drawn_card:3,deal_count:5}};
 b.context.fixture=ordinary;b.run('acceptSnapshot(fixture)');assert.equal(b.get('autoDeal'),true);
 const repeat={...ordinary,version:6};b.context.fixture=repeat;b.run('acceptSnapshot(fixture)');assert.equal(b.get('autoDeal'),true);
 b.context.fixture={...repeat,version:7,state:{...repeat.state,current_player:1,drawn_card:null,deal_count:6}};
 b.run('acceptSnapshot(fixture)');assert.equal(b.get('autoDeal'),true);
 b.context.fixture={...repeat,version:8,state:{...repeat.state,phase:'final_bidding'}};
 b.run('acceptSnapshot(fixture)');assert.equal(b.get('autoDeal'),false);
});

test('result explains staying at unplayed mandatory two after gaining the table',()=>{
 const b=browser(),data=ending();data.settlement.confirmed=true;
 data.state.result={...data.state.result,level:3,winner:1,banker:0,score:160,
  levels_before:[3,2],levels_after:[3,2],upgrade:2,mandatory_stop:2,next_banker:1};sit(b,data);
 assert.match(b.element('resultMessage').textContent,/必打 2 尚未坐庄打过/);
 assert.match(b.element('resultLevels').innerHTML,/金队 2 → <strong>2/);
});

test('current hand level uses the round level, not initial settings or other team level',()=>{
 const b=browser(),data=snapshot();data.state.level=5;data.state.levels=[5,2];sit(b,data);
 assert.equal(b.element('level').textContent,'5');assert.equal(b.element('mobileLevel').textContent,'5');
 assert.match(b.element('handTitle').textContent,/本局打 5/);
 const old={...data,version:3,state:{...data.state,level:2,round:0}};
 b.context.fixture=old;b.run('acceptSnapshot(fixture)');
 assert.equal(b.element('level').textContent,'5');assert.equal(b.get('snap.state.level'),5);
});

test('ordinary diamond five at level two is explained as diamonds, not trump',()=>{
 const b=browser(),data=snapshot();data.state.phase='playing';data.state.level=2;data.state.trump='H';
 data.state.hand=[32,53];data.state.trick=[{player:1,cards:[29]}];sit(b,data);
 assert.match(b.element('validationDetail').textContent,/首家 ♦5：本轮须跟 方片 1 张/);
 assert.doesNotMatch(b.element('validationDetail').textContent,/级牌/);
});

test('diamond level card explains why trump is required and shows actual round level',()=>{
 const b=browser(),data=snapshot();data.state.phase='playing';data.state.level=5;data.state.trump=null;
 data.state.hand=[32,53];data.state.trick=[{player:1,cards:[29]}];sit(b,data);
 assert.match(b.element('validationDetail').textContent,/须跟 主牌 1 张（打 5，♦5 是级牌）/);
 assert.match(b.element('handTitle').textContent,/本局打 5/);
});

test('stale validation refreshes authoritative level before enabling submit',async()=>{
 const b=browser(),data=snapshot();data.state.phase='playing';data.state.hand=[32,53];
 data.state.trick=[{player:1,cards:[29]}];sit(b,data);
 const pending=b.run('selected=new Set([32]);validateSelection()');
 const request=latest(b,'/api/validate');assert.equal(JSON.parse(request.options.body).version,4);
 assert.deepEqual(JSON.parse(request.options.body).context,{level:2,trump:null});
 request.reply({error:'STALE',message:'changed'},409);await new Promise(setImmediate);
 assert.equal(b.element('submitButton').disabled,true);
 const next={...data,version:5,state:{...data.state,level:5}};
 latest(b,'/api/state').reply(next);await pending;
 assert.equal(b.get('snap.state.level'),5);assert.equal(b.element('submitButton').disabled,true);
 latest(b,'/api/validate').reply({valid:false,level:5,trump:null,version:5,message:'须跟主牌'});
 await new Promise(setImmediate);assert.equal(b.element('submitButton').disabled,true);
});

test('conflicting validation level is synchronized instead of displaying mixed levels',async()=>{
 const b=browser(),data=snapshot();data.state.phase='playing';data.state.hand=[32,53];sit(b,data);
 const pending=b.run('selected=new Set([32]);validateSelection()');
 latest(b,'/api/validate').reply({valid:true,level:5,trump:null,version:4,message:'ok'});
 await new Promise(setImmediate);assert.equal(b.element('submitButton').disabled,true);
 latest(b,'/api/state').reply({...data,version:5,state:{...data.state,level:5}});await pending;
 assert.equal(b.get('snap.state.level'),5);assert.match(b.element('handTitle').textContent,/本局打 5/);
});

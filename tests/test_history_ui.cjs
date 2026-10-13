'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');

function page(){
 const elements=new Map(),requests=[],timers=new Map();let nextTimer=0;
 function element(id){
  if(!elements.has(id))elements.set(id,{hidden:false,value:id==='historyLevel'?'all':'',dataset:{},style:{setProperty(){}},
   innerHTML:'',textContent:'',disabled:false,clientWidth:900,classList:{toggle(){}},
   querySelectorAll(){return [];},setAttribute(){},removeAttribute(){},scrollIntoView(){}});
  return elements.get(id);
 }
 element('viewer').hidden=true;
 const context=vm.createContext({document:{getElementById:element,querySelectorAll(){return [];},addEventListener(){}},
  innerWidth:1200,window:{scrollTo(){},addEventListener(){}},location:{href:'http://127.0.0.1:8765/history.html',search:''},
  history:{replaceState(){}},URL,URLSearchParams,AbortController,
  setTimeout(fn,ms){const id=++nextTimer;timers.set(id,{fn,ms});return id;},clearTimeout(id){timers.delete(id);},
  TractorHandOrder:{sort:cards=>cards.slice()},
  fetch(url){let resolve;const promise=new Promise(r=>resolve=r);requests.push({url,reply(data,status=200){resolve({ok:status<400,json:async()=>data});}});return promise;}
 });
 for(const name of ['replay-clock.js','history.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../web',name),'utf8'),context);
 return {context,element,requests,timers,run:code=>vm.runInContext(code,context)};
}
function fixture(id='a'.repeat(32),level=11){
 const meta={id,level,mode:'pve',source:'live',round:1,finished_at:'2026-10-11T12:30:00+08:00',
  filename:'打J_2026-10-11_12-30.json',room:'r',trump:'H',tricks:10,steps:2,
  players:[0,1,2,3].map(seat=>({seat,name:seat===0?'<script>名字</script>':'玩家'+seat})),
  result:{banker:0,winner:0,score:30}};
 const state={phase:'dealing',level,trump:'H',round:1,current_player:0,banker:0,hands:[[1],[],[],[]],
  kitty:null,kitty_public:false,kitty_owner:null,bid:null,deal_count:1,redeals:0,trick:[],last_trick:null,
  trick_no:0,score:0,result:null,drawn_card:1};
 const frames=[{actor:null,action:null,events:[],state},
  {actor:0,action:{type:'pass'},events:[],state:{...state,current_player:1,hands:[[1],[2],[],[]],drawn_card:2}},
  {actor:1,action:{type:'pass'},events:[],state:{...state,current_player:2,hands:[[1],[2],[3],[]],drawn_card:3}}];
 return {meta,frames};
}
const flush=()=>new Promise(setImmediate);

test('replay dealing identifies this hand banker rather than the previous result; auction waits for a bid',()=>{
 const p=page(),data=fixture();p.context.meta=data.meta;p.context.fixture=data.frames[0];
 data.frames[0].state={...data.frames[0].state,round:2,auction:false,banker:2,result:{banker:0}};
 p.run('record={meta};renderTable(fixture)');
 assert.match(p.element('replayPlayers').innerHTML,/玩家2<\/span><b class="banker-badge">庄/);
 data.frames[0].state={...data.frames[0].state,round:1,auction:true,banker:null,result:null};
 p.run('renderTable(fixture)');assert.doesNotMatch(p.element('replayPlayers').innerHTML,/banker-badge/);
});

test('replay displays referee winner and the actor notice on a partial trick',()=>{
 const p=page(),data=fixture(),state={...data.frames[0].state,phase:'playing',trick_no:0,
  trick:[{player:0,cards:[7]},{player:3,cards:[19]}],trick_winner:3};
 p.context.fixture={state,actor:3,events:[{type:'play',player:3,notices:['ruff']}]};
 p.context.meta=data.meta;p.run('record={meta};renderTable(fixture)');
 assert.match(p.element('replayPlays').innerHTML,/play east[^\"]*winner/);
 assert.match(p.element('replayPlays').innerHTML,/当前最大/);
 assert.match(p.element('replayPlayers').innerHTML,/杀牌/);
});
async function load(p,data=fixture()){
 p.requests.find(r=>r.url==='/api/history').reply({records:[data.meta]});await flush();
 p.requests.find(r=>r.url==='/api/history/'+data.meta.id).reply(data);await flush();
 return data;
}
test('history preloads records and lists cards with escaped player names',async()=>{
 const p=page();await load(p);
 assert.equal(p.element('recordTotal').textContent,1);
 assert.match(p.element('historyList').innerHTML,/history-card/);
 assert.match(p.element('historyList').innerHTML,/&lt;script&gt;名字/);
 assert.doesNotMatch(p.element('historyList').innerHTML,/<script>名字/);
 assert.ok(p.requests.some(r=>r.url.startsWith('/api/history/')));
});
test('a selected replay loads once; all manual steps stay local and readonly',async()=>{
 const p=page(),data=await load(p);await p.run(`openRecord('${data.meta.id}')`);
 const requestCount=p.requests.length;p.element('replayNext').onclick();
 assert.equal(p.element('replayProgressText').textContent,'1 / 2');
 p.element('replayNext').onclick();assert.equal(p.element('replayProgressText').textContent,'2 / 2');
 assert.equal(p.requests.length,requestCount);assert.equal(p.element('replayNext').disabled,true);
 assert.equal(p.element('replayPlay').textContent,'↻ 再看一遍');
 assert.ok(p.requests.every(r=>r.url.startsWith('/api/history')));
});
test('replay shows bid cards until play and replaces them when countered',async()=>{
 const p=page(),data=fixture();
 data.frames[0].state={...data.frames[0].state,bid:{player:1,cards:[13],strength:1}};
 data.frames[1].state={...data.frames[1].state,bid:{player:2,cards:[52,106],strength:3},trump:null};
 data.frames[2].state={...data.frames[2].state,phase:'playing',bid:data.frames[1].state.bid};
 await load(p,data);await p.run(`openRecord('${data.meta.id}')`);
 assert.match(p.element('replayPlays').innerHTML,/play west declaration/);
 p.element('replayNext').onclick();
 assert.match(p.element('replayPlays').innerHTML,/play north declaration/);
 assert.doesNotMatch(p.element('replayPlays').innerHTML,/data-card="13"/);
 p.element('replayNext').onclick();assert.equal(p.element('replayPlays').innerHTML,'');
});
test('final replay step reveals eight bottom cards and rewinding hides that strip',async()=>{
 const p=page(),data=fixture();
 data.frames[2].state={...data.frames[2].state,phase:'round_end',
  settlement_kitty:[3,57,8,62,11,65,13,67],
  result:{banker:0,winner:1,score:150,captured:100,bottom:100,multiplier:2,penalty:-50,
   levels_before:[11,11],levels_after:[11,12]}};
 await load(p,data);await p.run(`openRecord('${data.meta.id}')`);
 assert.equal(p.element('replayBottom').hidden,true);
 p.element('replayNext').onclick();assert.equal(p.element('replayBottom').hidden,true);
 p.element('replayNext').onclick();
 assert.equal(p.element('replayBottom').hidden,false);
 assert.equal((p.element('replayBottom').innerHTML.match(/role="img"/g)||[]).length,8);
 assert.match(p.element('replaySettlement').innerHTML,/50 × 2 = 100/);
 assert.match(p.element('replayPrivacyText').textContent,/已公开/);
 p.element('replayPrev').onclick();assert.equal(p.element('replayBottom').hidden,true);
 assert.match(p.element('replayPrivacyText').textContent,/不明示/);
});
test('speed slider keeps fractional rates and reflects interval',async()=>{
 const p=page(),data=await load(p);await p.run(`openRecord('${data.meta.id}')`);
 p.element('replaySpeed').value='1.37';p.element('replaySpeed').oninput();
 assert.equal(p.element('replaySpeedValue').textContent,'1.37×');assert.equal(p.run('clock.speed'),1.37);
 p.element('replayPlay').onclick();assert.equal([...p.timers.values()].at(-1).ms,1000/1.37);
 p.element('resetSpeed').onclick();assert.equal(p.run('clock.speed'),1);
});
test('leaving history cancels playback and a late selected-record response',async()=>{
 const p=page();p.requests.find(r=>r.url==='/api/history').reply({records:[]});await flush();
 const data=fixture(),opening=p.run(`openRecord('${data.meta.id}')`);
 p.run('backToLibrary()');p.requests.find(r=>r.url==='/api/history/'+data.meta.id).reply(data);await opening;
 assert.equal(p.element('viewer').hidden,true);assert.equal(p.run('clock'),null);
});
test('a newer selection wins over an older response',async()=>{
 const p=page();p.requests.find(r=>r.url==='/api/history').reply({records:[]});await flush();
 const first=fixture('a'.repeat(32)),second=fixture('b'.repeat(32),13);
 const openingFirst=p.run(`openRecord('${first.meta.id}')`),openingSecond=p.run(`openRecord('${second.meta.id}')`);
 p.requests.find(r=>r.url.endsWith(second.meta.id)).reply(second);await openingSecond;
 p.requests.find(r=>r.url.endsWith(first.meta.id)).reply(first);await openingFirst;
 assert.equal(p.run('record.meta.id'),second.meta.id);assert.match(p.element('replayTitle').textContent,/K/);
});
test('filtering by level or search shows matching archive entries',async()=>{
 const p=page();await load(p);p.element('historyLevel').value='2';p.element('historyLevel').onchange();
 assert.equal(p.element('historyList').innerHTML,'');assert.match(p.element('historyStatus').textContent,/没有找到/);
 p.element('historyLevel').value='all';p.element('historySearch').value='名字';p.element('historySearch').oninput();
 assert.match(p.element('historyList').innerHTML,/history-card/);
});
test('following the active step scrolls only the timeline, never the whole page',async()=>{
 const p=page(),data=await load(p);await p.run(`openRecord('${data.meta.id}')`);
 const calls=[],pageScroll=[];
 p.context.window.scrollTo=options=>pageScroll.push(options);
 const container=p.element('replayTimeline');container.scrollTop=0;
 container.getBoundingClientRect=()=>({top:0,bottom:100});container.scrollTo=options=>calls.push(options);
 container.querySelectorAll=()=>[{dataset:{step:'1'},classList:{toggle(){}},setAttribute(){},removeAttribute(){},
  getBoundingClientRect:()=>({top:200,bottom:225})}];
 p.element('replayNext').onclick();assert.equal(calls[0].top,125);assert.equal(pageScroll.length,0);
});

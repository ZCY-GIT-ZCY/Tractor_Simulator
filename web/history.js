'use strict';
const $=id=>document.getElementById(id);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const suits=['S','H','D','C'],symbols=['♠','♥','♦','♣'];
const labels={11:'J',12:'Q',13:'K',14:'A',15:'小王',16:'大王'};
const label=r=>labels[r]||String(r);
const rank=c=>c%54<52?c%54%13+2:c%54-37;
const suit=c=>c%54<52?suits[Math.floor(c%54/13)]:null;
const red=c=>['H','D'].includes(suit(c));
const cardName=c=>rank(c)>14?label(rank(c)):symbols[suits.indexOf(suit(c))]+label(rank(c));
const modes={self:'自对弈',pve:'人机对局',pvp:'好友房间',evaluation:'AI 评测'};
const trumpName=t=>t?symbols[suits.indexOf(t)]+({S:'黑桃',H:'红桃',D:'方片',C:'草花'}[t]):'无主';
const dateText=iso=>new Date(iso).toLocaleDateString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit'});
const timeText=iso=>new Date(iso).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
let records=[],filter='all',record=null,clock=null,selectedSeat='auto',renderedIndex=-1,loadTicket=0;
const cached=new Map();

async function request(url){
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
 try{
  const response=await fetch(url,{signal:controller.signal});const data=await response.json();
  if(!response.ok)throw new Error(data.message||'读取失败，请稍后重试');return data;
 }finally{clearTimeout(timer);}
}
function getRecord(id){
 if(!cached.has(id)){
  while(cached.size>=6)cached.delete(cached.keys().next().value);
  const promise=request('/api/history/'+encodeURIComponent(id)).catch(error=>{if(cached.get(id)===promise)cached.delete(id);throw error;});
  cached.set(id,promise);
 }
 return cached.get(id);
}
function nameOf(seat){return record?.meta.players.find(p=>p.seat===seat)?.name||`${seat} 座`;}
function cardsText(cards){return cards.map(cardName).join(' ');}
function cardHTML(c,state,extra=''){
 const r=rank(c),symbol=r>14?'✦':symbols[suits.indexOf(suit(c))];
 const corner=r>14?label(r):`${label(r)}<span>${symbol}</span>`;
 const main=r>=11&&r<=13?label(r):symbol;
 const trump=r>14||r===state.level||suit(c)===state.trump;
 return `<div class="card ${red(c)?'red':''} ${r>14?'joker':''} ${extra}" title="${cardName(c)}" aria-label="${cardName(c)}" role="img"><div class="corner">${corner}</div><div class="card-center">${main}</div>${trump?'<i class="trump-dot"></i>':''}</div>`;
}
function cardChips(cards){return cards.map(c=>`<span class="${red(c)?'red':''}">${cardName(c)}</span>`).join(' ');}
function recordCard(meta){
 const r=meta.result,winning=r.winner?'金队':'青队',outcome=r.winner===r.banker%2?'守庄':'上台';
 return `<button class="history-card" data-record="${esc(meta.id)}" aria-label="回放打 ${label(meta.level)}，${esc(dateText(meta.finished_at))} ${esc(timeText(meta.finished_at))}"><div class="record-top"><span class="record-mode">${modes[meta.mode]||'牌桌对局'}</span><span class="record-time">${esc(timeText(meta.finished_at))}</span></div><div class="record-body"><div class="record-level"><span>打</span><strong>${label(meta.level)}</strong><small>${meta.trump?symbols[suits.indexOf(meta.trump)]:'无主'}</small></div><div class="record-outcome ${r.winner?'gold':''}"><strong>${r.score}<small> 分</small></strong>${winning}${outcome}</div></div><div class="record-players">${meta.players.map(p=>`<span>${p.seat%2?'◌':'•'} ${esc(p.name)}</span>`).join('')}</div><div class="record-footer"><span>${meta.tricks} 轮 · ${meta.steps} 步${meta.source==='imported-evaluation'?' · 评测导入':''}</span><b>回看这一局 ↗</b></div></button>`;
}
function renderLibrary(){
 const query=$('historySearch').value.trim().toLocaleLowerCase(),level=$('historyLevel').value;
 const visible=records.filter(m=>(filter==='all'||m.mode===filter)&&(level==='all'||m.level===Number(level))&&
  (!query||[m.filename,m.room,dateText(m.finished_at),...m.players.map(p=>p.name)].join(' ').toLocaleLowerCase().includes(query)));
 $('historyStatus').hidden=visible.length>0;
 if(!visible.length)$('historyStatus').textContent=records.length?'没有找到这类对局。试试其他等级或牌友名字。':'还没有入档的对局。\n回牌桌打完一局，结算后会自动出现在这里。';
 const groups=new Map();
 for(const meta of visible){const date=dateText(meta.finished_at);if(!groups.has(date))groups.set(date,[]);groups.get(date).push(meta);}
 $('historyList').innerHTML=Array.from(groups,([date,items])=>`<section><div class="history-date">${esc(date)} <span>· ${items.length} 局</span></div><div class="record-grid">${items.map(recordCard).join('')}</div></section>`).join('');
 for(const button of $('historyList').querySelectorAll('[data-record]')){
  button.onclick=()=>openRecord(button.dataset.record);
  button.onpointerenter=button.onfocus=()=>getRecord(button.dataset.record).catch(()=>{});
 }
}
async function loadLibrary(){
 $('refreshHistory').disabled=true;
 try{
  const data=await request('/api/history');records=data.records;$('recordTotal').textContent=records.length;renderLibrary();
  // Warm the newest two records. Every selected replay loads all its steps once.
  for(const meta of records.slice(0,2))getRecord(meta.id).catch(()=>{});
 }catch(error){$('historyStatus').hidden=false;$('historyStatus').textContent='暂时无法读取历史对局。\n'+error.message;}
 finally{$('refreshHistory').disabled=false;}
}
function phaseText(phase){return {dealing:'摸牌与亮主',final_bidding:'拿底前确认',burying:'扣底',countering:'扣底后反底',playing:'出牌',round_end:'本局结算',match_end:'比赛结束'}[phase]||phase;}
function describeFrame(index){
 const f=record.frames[index],s=f.state,a=f.action,previous=record.frames[index-1]?.state;
 let title='',detail='',cards=[];
 if(!a){title='开始摸牌 · 打 '+label(s.level);detail=`${nameOf(s.current_player)} 摸到第一张牌`;cards=s.drawn_card==null?[]:[s.drawn_card];}
 else if(a.type==='play'){
  const failed=f.events.find(e=>e.type==='throw_failed');
  title=nameOf(f.actor)+' · '+(failed?'甩牌失败':previous?.trick.length?'跟牌':'领出');cards=a.cards;
  detail=failed?`尝试 ${cardsText(failed.attempted)}\n罚 ${failed.penalty} 分，实际出 ${cardsText(failed.cards)}`:`出 ${a.cards.length} 张 · ${cardsText(a.cards)}`;
 }else if(a.type==='bid'){
  title=nameOf(f.actor)+' · '+(previous?.bid?previous.bid.player===f.actor?'加固主牌':'反主':'亮主');cards=a.cards;
  detail=`${cardsText(a.cards)} · ${trumpName(s.trump)}`;
 }else if(a.type==='bury'){title=nameOf(f.actor)+' · 扣底';detail='扣下八张牌，底牌不明示。';}
 else if(a.type==='pass'){
  title=nameOf(f.actor)+' · '+(previous?.phase==='dealing'?'不亮':previous?.phase==='final_bidding'&&previous?.bid?.player===f.actor?'不加固':'不反');
  detail=s.phase==='dealing'?`${nameOf(s.current_player)} 摸到下一张牌`:phaseText(s.phase);
  cards=s.phase==='dealing'&&s.drawn_card!=null?[s.drawn_card]:[];
 }
 for(const event of f.events){
  if(event.type==='kitty_picked_up')detail+='\n'+nameOf(event.player)+' 拿起当前八张底牌';
  if(event.type==='bottom_revealed')detail+='\n无人亮主，翻底定主；八张底牌公开留底';
  if(event.type==='play_started')detail+='\n'+nameOf(event.banker)+' 领出第一轮';
  if(event.type==='redeal')detail+='\n无人亮主抢庄，收牌重新洗发';
  if(event.type==='trick_ended')detail+=`\n第 ${event.number} 轮：${nameOf(event.winner)} 赢得 ${event.points} 分`;
  if(event.type==='round_ended')detail+='\n本局结束 · 闲家最终 '+event.score+' 分';
 }
 return {title,detail:detail.trim(),cards};
}
function buildTimeline(){
 $('timelineCount').textContent=record.meta.steps+' 步';
 $('replayTimeline').innerHTML=record.frames.map((f,index)=>{
  const action=describeFrame(index),marker=index>0&&f.state.phase!==record.frames[index-1].state.phase;
  return `<button class="timeline-step ${marker?'marker':''}" data-step="${index}"><span>${String(index).padStart(2,'0')}</span><div><strong>${esc(action.title)}</strong><small>${action.cards.length?cardChips(action.cards):esc(phaseText(f.state.phase))}</small></div></button>`;
 }).join('');
 for(const button of $('replayTimeline').querySelectorAll('[data-step]'))button.onclick=()=>clock.seek(Number(button.dataset.step));
}
function renderTable(f){
 const s=f.state,banker=s.result?.banker??s.banker,actor=f.actor??s.current_player;
 const handSort=cards=>TractorHandOrder.sort(cards,s.level,s.trump);
 $('replayPlayers').innerHTML=record.meta.players.map(p=>{
  const pos=['south','west','north','east'][p.seat],cards=handSort(s.hands[p.seat]);
  const hand=!cards.length?'':['north','south'].includes(pos)?`<div class="mini-hand">${cards.map(c=>cardHTML(c,s)).join('')}</div>`:
   `<div class="side-hand">${cards.map(c=>`<div class="card-chip ${red(c)?'red':''}">${label(rank(c))}<span>${rank(c)>14?'✦':symbols[suits.indexOf(suit(c))]}</span></div>`).join('')}</div>`;
  return `<div class="seat ${pos} ${actor===p.seat?'active':''}"><div class="player-badge"><div class="avatar">${p.seat}</div><div class="player-text"><strong>${esc(p.name)}${p.seat===banker?'<b class="banker-badge">庄</b>':''}</strong><small>${cards.length} 张 · ${p.seat%2?'金队':'青队'}</small></div></div>${hand}</div>`;
 }).join('');
 const declaration=['dealing','final_bidding','burying','countering'].includes(s.phase);
 const plays=declaration?(s.bid?[s.bid]:[]):s.trick.length?s.trick:s.last_trick?.plays||[];
 $('replayPlays').innerHTML=plays.map((p,i)=>{
  const pos=['south','west','north','east'][p.player],width=$('replayTable').clientWidth,cardWidth=innerWidth<=760?32:44;
  const step=Math.min(cardWidth-18,Math.max(5,(width*(['north','south'].includes(pos)?0.72:0.36)-cardWidth)/Math.max(1,p.cards.length-1)));
  return `<div class="play ${pos} ${declaration?'declaration':''} ${s.trick.length&&i===plays.length-1?'fresh':''} ${!declaration&&!s.trick.length&&s.last_trick?.winner===p.player?'winner':''}" style="--play-overlap:${step-cardWidth}px">${p.cards.map(c=>cardHTML(c,s)).join('')}${declaration?`<span class="declaration-label">${trumpName(s.trump)} · 已亮牌</span>`:''}</div>`;
 }).join('');
 $('replayTableStatus').textContent=['round_end','match_end'].includes(s.phase)?'本局已结束':s.phase==='playing'?(s.trick.length?`${nameOf(s.current_player)} 须跟 ${s.trick[0].cards.length} 张`:`${nameOf(s.current_player)} 领出`):`${phaseText(s.phase)} · ${nameOf(s.current_player)}`;
 $('replayKitty').innerHTML=s.kitty_public&&s.kitty?.length?`公开底牌<div style="display:flex">${s.kitty.map(c=>cardHTML(c,s)).join('')}</div>`:s.phase==='burying'?'底牌已收入手中':'底牌 · 不明示';
 const ended=['round_end','match_end'].includes(s.phase),bottom=ended?s.settlement_kitty:null;
 $('replayTable').classList.toggle('reviewing',!!bottom);$('replayBottom').hidden=!bottom;$('replayKitty').hidden=!!bottom;
 $('replayBottom').innerHTML=bottom?`<span>本局底牌 · 八张</span><div class="settlement-cards">${bottom.map(c=>cardHTML(c,s)).join('')}</div>`:'';
 $('replayPrivacyText').textContent=bottom?'四家手牌可见 · 本局底牌已公开':s.kitty_public?'四家手牌可见 · 翻底定主，底牌已公开':'四家手牌可见 · 对局中底牌不明示';
 $('replayLevel').textContent=label(s.level);$('replayTrump').textContent=s.phase==='dealing'&&!s.bid?'待亮主':trumpName(s.trump);
 $('replayScore').textContent=s.score;$('replayTrick').textContent=s.phase==='playing'?`第 ${s.trick_no+1} 轮`:phaseText(s.phase);
 renderHand();
}
function renderHand(){
 if(!record||!clock)return;
 const f=record.frames[clock.index],s=f.state,seat=selectedSeat==='auto'?(f.actor??s.current_player):Number(selectedSeat);
 const cards=TractorHandOrder.sort(s.hands[seat],s.level,s.trump);
 $('replayHandTitle').textContent=`${nameOf(seat)} · ${cards.length} 张手牌`;
 const width=innerWidth<=760?53:61;
 const gap=Math.max(19,Math.min(width,($('replayHand').clientWidth-width)/Math.max(1,cards.length-1)));
 $('replayHand').style.setProperty('--overlap',`${gap-width}px`);
 $('replayHand').innerHTML=cards.length?cards.map(c=>cardHTML(c,s)).join(''):'<span class="small-empty">手牌已出完</span>';
}
function renderSettlement(state){
 const r=state.result;$('replaySettlement').hidden=!r;if(!r)return;
 const base=state.settlement_kitty?.reduce((sum,c)=>sum+(rank(c)===5?5:[10,13].includes(rank(c))?10:0),0);
 const bottom=base!=null?`${base} × ${r.multiplier} = ${r.bottom}`:`${r.bottom}（×${r.multiplier}）`;
 $('replaySettlement').innerHTML=`<h3>${r.winner?'金队':'青队'}${r.winner===r.banker%2?'守庄成功':'上台坐庄'}</h3><strong>${r.score}</strong> <small>分</small><p>抓分 ${r.captured} · 底分 ${bottom}<br>罚分 ${r.penalty>0?'+':''}${r.penalty}<br>青队 ${label(r.levels_before[0])} → ${label(r.levels_after[0])} &nbsp; 金队 ${label(r.levels_before[1])} → ${label(r.levels_after[1])}${r.downgrade?'<br>'+label(r.level)+' 扣底，庄家退回 '+label(r.downgrade):''}</p>`;
}
function updatePlayback(){
 if(!clock||!record)return;
 $('replayProgress').value=clock.index;$('replayProgressText').textContent=clock.index+' / '+(clock.count-1);
 $('replayPrev').disabled=clock.index===0;$('replayNext').disabled=clock.index===clock.count-1;
 $('replayPlay').textContent=clock.running?'Ⅱ 暂停播放':clock.index===clock.count-1?'↻ 再看一遍':'▶ 自动播放';
 const speed=Number(clock.speed.toFixed(2));$('replaySpeed').value=speed;$('replaySpeedValue').textContent=speed+'×';
 $('replayTiming').textContent=`每 ${Number((1/speed).toFixed(2))} 秒一步`;
 if(renderedIndex===clock.index)return;renderedIndex=clock.index;
 const f=record.frames[clock.index],action=describeFrame(clock.index);renderTable(f);renderSettlement(f.state);
 $('replayActionTitle').textContent=action.title;$('replayActionDetail').textContent=action.detail;
 $('replayActionCards').innerHTML=action.cards.map(c=>cardHTML(c,f.state)).join('');
 for(const button of $('replayTimeline').querySelectorAll('[data-step]')){
  const active=Number(button.dataset.step)===clock.index;button.classList.toggle('active',active);
  if(active){
   button.setAttribute('aria-current','step');
   const container=$('replayTimeline'),row=button.getBoundingClientRect(),box=container.getBoundingClientRect();
   // Scroll the timeline itself; never pull a phone away from its controls.
   if(row.top<box.top||row.bottom>box.bottom)container.scrollTo({
    top:container.scrollTop+(row.top<box.top?row.top-box.top:row.bottom-box.bottom),
    behavior:clock.running&&clock.speed<=2?'smooth':'instant'});
  }else button.removeAttribute('aria-current');
 }
}
async function openRecord(id){
 const ticket=++loadTicket;clock?.pause();$('historyStatus').hidden=false;$('historyStatus').textContent='正在展开这局牌…';
 try{
  const data=await getRecord(id);if(ticket!==loadTicket)return;
  if(!Array.isArray(data.frames)||!data.frames.length)throw new Error('这份记录没有完整的回放步骤');
  clock?.dispose();record=data;renderedIndex=-1;selectedSeat='auto';
  $('library').hidden=true;$('viewer').hidden=false;$('historyStatus').hidden=true;
  $('replayTitle').textContent='打 '+label(record.meta.level)+' · 第 '+record.meta.round+' 局';
  $('replayMeta').textContent=dateText(record.meta.finished_at)+' '+timeText(record.meta.finished_at)+' · '+record.meta.tricks+' 轮'+(record.meta.source==='imported-evaluation'?' · 已完成的 AI 评测对局':'');
  $('replayMode').textContent=modes[record.meta.mode]||'牌桌对局';
  $('replayProgress').max=record.frames.length-1;
  for(const button of document.querySelectorAll('[data-seat]'))button.classList.toggle('active',button.dataset.seat===selectedSeat);
  buildTimeline();clock=new TractorReplayClock(record.frames.length,updatePlayback);updatePlayback();
  const url=new URL(location.href);url.searchParams.set('id',id);history.replaceState(null,'',url);
  window.scrollTo({top:0,behavior:'instant'});
 }catch(error){if(ticket===loadTicket){$('historyStatus').hidden=false;$('historyStatus').textContent=error.message;}}
}
function backToLibrary(){
 loadTicket++;clock?.dispose();clock=null;record=null;$('viewer').hidden=true;$('library').hidden=false;
 const url=new URL(location.href);url.searchParams.delete('id');history.replaceState(null,'',url);renderLibrary();
}
$('historyLevel').innerHTML+=[...Array(13)].map((_,i)=>`<option value="${i+2}">打 ${label(i+2)}</option>`).join('');
for(const button of document.querySelectorAll('[data-filter]'))button.onclick=()=>{
 filter=button.dataset.filter;for(const b of document.querySelectorAll('[data-filter]'))b.classList.toggle('active',b===button);renderLibrary();
};
$('historySearch').oninput=$('historyLevel').onchange=renderLibrary;
$('refreshHistory').onclick=loadLibrary;$('backHistory').onclick=backToLibrary;
$('replayPrev').onclick=()=>clock?.step(-1);$('replayNext').onclick=()=>clock?.step(1);
$('replayPlay').onclick=()=>clock&&(clock.running?clock.pause():clock.play());
$('replaySpeed').oninput=()=>clock?.rate($('replaySpeed').value);$('resetSpeed').onclick=()=>clock?.rate(1);
$('replayProgress').oninput=()=>clock?.seek($('replayProgress').value);
$('skipDealing').onclick=()=>{if(clock){const index=record.frames.findIndex(f=>f.state.phase==='playing');if(index>=0)clock.seek(index);}};
for(const button of document.querySelectorAll('[data-seat]'))button.onclick=()=>{
 selectedSeat=button.dataset.seat;for(const b of document.querySelectorAll('[data-seat]'))b.classList.toggle('active',b===button);renderHand();
};
document.addEventListener('keydown',event=>{
 if(!clock||$('viewer').hidden||['INPUT','SELECT','TEXTAREA','BUTTON','SUMMARY'].includes(event.target.tagName))return;
 if(event.key==='ArrowRight'){event.preventDefault();clock.step(1);}
 if(event.key==='ArrowLeft'){event.preventDefault();clock.step(-1);}
 if(event.key===' '){event.preventDefault();clock.running?clock.pause():clock.play();}
 if(event.key==='Escape')backToLibrary();
});
document.addEventListener('visibilitychange',()=>{if(document.hidden)clock?.pause();});
window.addEventListener('resize',()=>{if(record&&clock)renderTable(record.frames[clock.index]);});
loadLibrary();
const initialID=new URLSearchParams(location.search).get('id');if(initialID)openRecord(initialID);

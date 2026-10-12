'use strict';
const $=id=>document.getElementById(id);
const suits=['S','H','D','C'], symbols=['♠','♥','♦','♣'];
const labels={11:'J',12:'Q',13:'K',14:'A',15:'小王',16:'大王'};
const mandatory=new Set([2,5,10,11,13,14]);
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const face=id=>id%54, rank=id=>face(id)<52?face(id)%13+2:face(id)-37;
const suit=id=>face(id)<52?suits[Math.floor(face(id)/13)]:null;
const label=r=>labels[r]||String(r);
const cardName=id=>rank(id)>14?label(rank(id)):symbols[suits.indexOf(suit(id))]+label(rank(id));
let mode='self', token=sessionStorage.getItem('tractor_session'), snap=null, selected=new Set(), busy=false;
let handKey='', selectionScopeKey='', forcedSelectionKey='', validationTicket=0, pollGeneration=0, closedResult=null, autoDeal=false, autoTimer=null, toastTimer;
let previousHands=[new Set(),new Set(),new Set(),new Set()], previousPlay='';
let drawTurnKey='', visibleDraw=null;
let bidNoticeTimer;
let biddingTimer=null,biddingEndAt=0;
let tentativeSeat=null,seatBusy=false,roomManagerOpen=false;
const seatDirections=['南','西','北','东'];
let publicURL='', remembered=null;
const invitedRoom=new URLSearchParams(location.search).get('room');
try{remembered=JSON.parse(localStorage.getItem('tractor_resume')||'null');}catch{}
if(!remembered||typeof remembered.token!=='string'||typeof remembered.room!=='string')remembered=null;
function updateResumeButton(){const available=remembered&&(!invitedRoom||remembered.room===invitedRoom);$('resumeButton').hidden=!available;$('resumeButton').textContent='回到上次牌桌'+(remembered?.mode==='pvp'?' · '+remembered.room:'');}
function rememberSeat(){remembered={token,room:snap.room,mode:snap.mode,name:snap.name||nameOf(snap.seat)};try{localStorage.setItem('tractor_resume',JSON.stringify(remembered));}catch{}}
function forgetSeat(sessionToken){if(remembered?.token===sessionToken)remembered=null;try{if(JSON.parse(localStorage.getItem('tractor_resume')||'null')?.token===sessionToken)localStorage.removeItem('tractor_resume');}catch{}updateResumeButton();}
function sessionScope(){return {token,generation:pollGeneration};}
function currentSession(scope){return scope.token===token&&scope.generation===pollGeneration;}
function inviteLink(){const url=new URL(publicURL||location.origin);url.searchParams.set('room',snap.room);return url.href;}
function showInvite(){if(!snap||mode!=='pvp')return;$('inviteURL').value=inviteLink();$('inviteOverlay').hidden=false;}
async function copyText(value,message){try{await navigator.clipboard.writeText(value);toast(message);}catch{showInvite();toast('长按链接或全选后复制');}}

async function api(path,data){
 const controller=new AbortController(); const timeout=setTimeout(()=>controller.abort(),12000);
 try{
  const response=await fetch(path,{method:data===undefined?'GET':'POST',headers:{...(data===undefined?{}:{'Content-Type':'application/json'}),...(token?{'Authorization':'Bearer '+token}:{})},body:data===undefined?undefined:JSON.stringify(data),signal:controller.signal});
  const result=await response.json();
  if(!response.ok){const e=new Error(result.message||'请求失败');e.code=result.code||result.error;e.status=response.status;throw e;}
  return result;
 }finally{clearTimeout(timeout);}
}
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,3500);}
function nameOf(seat){return seat==null?snap?.name||'待选座':snap?.players[seat]?.name||`${seat} 座`;}
function position(seat){return ['south','west','north','east'][(seat-(snap?.mode==='self'?0:snap?.seat||0)+4)%4];}
function isMyTurn(){return !!snap?.started&&!!snap.state&&!snap.paused&&snap.controlled.includes(snap.state.current_player);}
function isTrump(id){const s=snap?.state;return s&&(rank(id)>14||rank(id)===s.level||(s.trump&&suit(id)===s.trump));}
function leadRequirement(){
 const s=snap?.state,lead=s?.trick?.[0]?.cards;
 if(!lead?.length)return '';
 const c=lead[0],door=isTrump(c)?'主牌':({S:'黑桃',H:'红桃',D:'方片',C:'草花'}[suit(c)]);
 const explanation=rank(c)===s.level?`（打 ${label(s.level)}，${cardName(c)} 是级牌）`:s.trump&&suit(c)===s.trump?'（本局主花色）':'';
 return `首家 ${lead.map(cardName).join('、')}：本轮须跟 ${door} ${lead.length} 张${explanation}`;
}
function displayHand(cards){const s=snap?.state;return TractorHandOrder.sort(cards,s?.level??2,s?.trump??null);}
function cardHTML(id,classes=''){
 const r=rank(id),symbol=r>14?'✦':symbols[suits.indexOf(suit(id))];
 const corner=r>14?label(r):`${label(r)}<span>${symbol}</span>`;
 const center=r>=11&&r<=13?`<span>${label(r)}</span>`:symbol;
 return `<div class="card ${suit(id)==='H'||suit(id)==='D'?'red':''} ${r>14?'joker':''} ${r>=11&&r<=13?'royal':''} ${classes}" data-card="${id}" title="${cardName(id)} · ${id>=54?'第二副':'第一副'}" aria-label="${cardName(id)}" role="img"><div class="corner">${corner}</div><div class="card-center">${center}</div><div class="corner bottom">${corner}</div>${isTrump(id)?'<i class="trump-dot"></i>':''}</div>`;
}
function renderTeam(id,team){
 const s=snap?.state,l=s?.levels[team]||2,banker=s&&(s.phase==='round_end'||s.phase==='match_end'?s.result?.banker:s.banker);
 $(id).className='team-block '+(team?'gold':'');
 $(id).innerHTML=`<div class="team-label"><span><i class="team-dot ${team?'gold':''}"></i>${team?'金队':'青队'} ${banker!=null&&banker%2===team?'· 庄家方':''}</span><strong>${label(l)}</strong></div><div class="level-pips">${Array.from({length:13},(_,i)=>`<i title="${label(i+2)}${mandatory.has(i+2)?' · 必打':''}" class="${i+2<l?'done':i+2===l?'current':''} ${mandatory.has(i+2)?'mandatory':''}"></i>`).join('')}</div>`;
}
function renderPlayers(){
 const s=snap.state,banker=['round_end','match_end'].includes(s.phase)?s.result?.banker:s.banker;
 $('playersLayer').innerHTML=snap.players.map(p=>{
  const pos=position(p.seat),active=s.current_player===p.seat&&!['round_end','match_end'].includes(s.phase);
  const raw=snap.mode==='self'?s.hands[p.seat]:p.seat===snap.seat?s.hand:null;
  const visible=raw?displayHand(raw):null;
  let hand='';
  if(visible?.length&&(pos!=='south'||snap.mode==='self')){
   hand=['north','south'].includes(pos)?`<div class="mini-hand">${visible.map(c=>cardHTML(c,previousHands[p.seat].has(c)?'':'new')).join('')}</div>`:
     `<div class="side-hand">${visible.map(c=>`<div class="card-chip ${suit(c)==='H'||suit(c)==='D'?'red':''}" title="${cardName(c)}">${label(rank(c))}<span>${rank(c)>14?'✦':symbols[suits.indexOf(suit(c))]}</span></div>`).join('')}</div>`;
  }else if(pos!=='south'&&s.hand_counts[p.seat])hand='<div class="hidden-stack"></div>';
  if(visible)previousHands[p.seat]=new Set(visible);
  return `<div class="seat ${pos} ${active?'active':''}"><div class="player-badge"><div class="avatar">${p.seat}</div><div class="player-text"><strong>${esc(p.name)}${banker===p.seat?'<b class="banker-badge">庄</b>':''}</strong><small>${s.hand_counts[p.seat]} 张 · ${p.seat%2?'金队':'青队'}${!p.online?' · 离线':''}</small></div></div>${hand}</div>`;
 }).join('');
}
function renderPlays(){
 const s=snap.state,declaration=['dealing','final_bidding','burying','countering'].includes(s.phase);
 const show=declaration?(s.bid?[s.bid]:[]):s.trick.length?s.trick:s.last_trick?.plays||[];
 const current=JSON.stringify(show),fresh=current!==previousPlay&&s.trick.length;
 $('playsLayer').innerHTML=show.map(p=>{
  const pos=position(p.player),width=$('table').clientWidth,cardWidth=innerWidth<=760?32:44;
  const step=Math.min(cardWidth-18,Math.max(5,(width*(['north','south'].includes(pos)?0.72:0.36)-cardWidth)/Math.max(1,p.cards.length-1)));
  return `<div class="play ${pos} ${declaration?'declaration':''} ${fresh&&p===show[show.length-1]?'fresh':''} ${!declaration&&!s.trick.length&&s.last_trick?.winner===p.player?'winner':''}" style="--play-overlap:${step-cardWidth}px">${p.cards.map(c=>cardHTML(c)).join('')}${declaration?`<span class="declaration-label">${s.trump?symbols[suits.indexOf(s.trump)]+' 主':'无主'} · 已亮牌</span>`:''}</div>`;
 }).join('');
 previousPlay=current;
}
function notifyBids(previous){
 const s=snap.state;
 if(!s||!previous?.state||previous.room!==snap.room||previous.state.round!==s.round)return;
 const before=previous.state.public_history||previous.state.events||[],seen=new Set(before.filter(e=>e.type==='bid').map(e=>e.seq));
 const events=(s.public_history||s.events||[]).filter(e=>e.type==='bid'&&!seen.has(e.seq));
 for(const e of events){
  if(e.player===snap.seat)continue;
  const earlier=(s.public_history||s.events||[]).filter(b=>b.type==='bid'&&b.seq<e.seq).at(-1);
  const action=!earlier?'亮主':earlier.player===e.player?'加固':'反主';
  const trump=e.trump?symbols[suits.indexOf(e.trump)]+' '+({S:'黑桃',H:'红桃',D:'方片',C:'草花'}[e.trump]):'无主';
  $('bidNotice').innerHTML=`<span class="bid-notice-icon ${['H','D'].includes(e.trump)?'red':''}">${e.trump?symbols[suits.indexOf(e.trump)]:'✦'}</span><div><strong>${esc(nameOf(e.player))} ${action}</strong><small>本局主牌：${trump}</small></div>`;
  $('bidNotice').hidden=false;clearTimeout(bidNoticeTimer);bidNoticeTimer=setTimeout(()=>$('bidNotice').hidden=true,3600);
 }
}
function renderSettlement(){
 const s=snap.state,r=s.result,review=snap.settlement;
 const visible=['round_end','match_end'].includes(s.phase)&&!!r&&!!review;
 $('settlementPanel').hidden=$('settlementBottom').hidden=!visible;$('table').classList.toggle('reviewing',visible);
 if(!visible)return;
 $('settlementBottom').innerHTML=`<span>本局底牌 · 八张</span><div class="settlement-cards">${review.kitty.map(c=>cardHTML(c)).join('')}</div>`;
 $('settlementFormula').innerHTML=`<div class="review-heading">底牌与计分核对 <small>${esc(nameOf(r.banker))} 坐庄</small></div><div class="score-equation"><span>抓分 <b>${r.captured}</b></span><i>＋</i><span>底分 <b>${review.kitty_points} × ${r.multiplier} = ${r.bottom}</b></span><i>${r.penalty<0?'－':'＋'}</i><span>甩牌罚分 <b>${Math.abs(r.penalty)}</b></span><i>＝</i><span class="score-total"><b>${r.score}</b> 分</span></div><small>${r.multiplier===0?'庄家方赢末轮，底牌不计入闲家得分。':`闲家方赢末轮，底牌原始分数 ${review.kitty_points}，扣底倍率 ×${r.multiplier}。`}</small>`;
 $('confirmResultButton').hidden=!review.can_confirm||review.confirmed;
 $('confirmResultButton').disabled=busy;
 $('settlementWaiting').textContent=review.confirmed?'本局庄家已确认 · 点击阶段标签可再次查看结算':review.can_confirm?'核对后确认，四家将同时收到结算':review.ai_banker?'庄家 AI 核对中 · 底牌展示至少 6 秒后确认':`等待本局庄家 ${nameOf(r.banker)} 确认`;
 $('kittyArea').hidden=true;
}
function activeHand(){return displayHand(snap.mode==='self'?snap.state.hands[snap.state.current_player]:snap.state.hand);}
function dealingComplete(){return snap?.state?.phase==='dealing'&&!!snap.state.bidding_window?.complete;}
function canBidNow(){return !!snap?.started&&!!snap.state&&!snap.paused&&snap.state.phase==='dealing'&&!!snap.state.realtime_bidding&&(!dealingComplete()||biddingEndAt>Date.now())&&(mode==='self'||snap.controlled.includes(snap.seat));}
function updateBiddingCountdown(){
 clearTimeout(biddingTimer);
 if(!dealingComplete())return;
 const remaining=snap.paused?snap.state.bidding_window.remaining_seconds:Math.max(0,(biddingEndAt-Date.now())/1000);
 $('tableStatus').textContent=snap.paused?'实时亮主等待已暂停，重连后继续':remaining>0?`摸牌结束 · 还可亮主 ${remaining.toFixed(1)} 秒`:'实时亮主结束 · 正在定主';
 if(!snap.paused&&remaining<=0){++validationTicket;$('submitButton').disabled=true;return;}
 if(!snap.paused)biddingTimer=setTimeout(updateBiddingCountdown,100);
}
function canSelectHand(){return !!snap?.started&&!!snap.state&&!snap.paused&&!busy&&(isMyTurn()&&!['round_end','match_end'].includes(snap.state.phase)||mode!=='self'&&['playing','dealing'].includes(snap.state.phase));}
function toggleCard(c){
 if(!canSelectHand()||!activeHand().includes(c))return;
 autoDeal=false;clearTimeout(autoTimer);updateAutoDeal();
 selected.has(c)?selected.delete(c):selected.add(c);syncSelection();validateSelection();
}
function renderHand(){
 const h=activeHand(),enabled=canSelectHand();
 $('hand').className='full-hand '+(!enabled?'disabled':'')+(enabled&&!isMyTurn()?' preselecting':'');
 $('hand').innerHTML=h.length?h.map(c=>cardHTML(c,selected.has(c)?'selected':'')).join(''):'<span class="small-empty">'+(['round_end','match_end'].includes(snap.state.phase)?'本局手牌已出完':'正在摸牌，稍等片刻')+'</span>';
 layoutHand();
 if(enabled){for(const element of $('hand').querySelectorAll('[data-card]')){
  element.role='button';element.tabIndex=0;element.setAttribute('aria-pressed',selected.has(Number(element.dataset.card)));
  const toggle=()=>toggleCard(Number(element.dataset.card));
  element.addEventListener('click',toggle);element.addEventListener('keydown',e=>{if(e.key===' '||e.key==='Enter'){e.preventDefault();e.stopPropagation();toggle();}});
 }}
}
function layoutHand(){
 const cards=$('hand').querySelectorAll('[data-card]');
 const plan=TractorHandLayout.fit($('hand').clientWidth,cards.length,innerWidth<=760);
 $('hand').style.setProperty('--hand-card-width',`${plan.cardWidth}px`);
 cards.forEach((card,i)=>{
  const pos=plan.positions[i];
  card.style.setProperty('--hand-x',`${pos.x}px`);card.style.setProperty('--hand-y',`${pos.y}px`);
  card.style.setProperty('--hand-index-size',`${pos.indexSize}px`);
 });
}
function syncSelection(){for(const e of $('hand').querySelectorAll('[data-card]')){const on=selected.has(Number(e.dataset.card));e.classList.toggle('selected',on);if(e.role==='button')e.setAttribute('aria-pressed',on);}}
function renderDraw(){
 const s=snap?.state,p=mode==='self'?s?.current_player:snap?.seat;
 const key=s?.phase==='dealing'?`${snap.room}|${s.round}|${s.redeals}|${s.deal_count}|${s.current_player}`:'';
 if(key!==drawTurnKey){visibleDraw=TractorDrawPreview.cardFor(s,p,previousHands[p]);drawTurnKey=key;}
 if(!key)visibleDraw=null;
 const preview=$('drawPreview'),host=$(innerWidth<=1150?'drawPreviewMobile':'drawPreviewDesktop');
 if(preview.parentElement!==host)host.appendChild(preview);
 preview.hidden=visibleDraw==null;
 const cardKey=visibleDraw==null?'':String(visibleDraw);
 if($('drawCard').dataset.draw!==cardKey){$('drawCard').innerHTML=visibleDraw==null?'':cardHTML(visibleDraw,'new');$('drawCard').dataset.draw=cardKey;}
}
function eventText(e){
 const who=e.player!=null?esc(nameOf(e.player)):'';
 switch(e.type){
 case 'round_started':return [`第 ${e.round} 局`,'开始摸牌 · 打 '+label(e.level)];
 case 'bid':return ['亮主 / 反主',who+' 亮出 '+e.cards.map(cardName).join(' ')];
 case 'final_bidding_started':return ['拿底前确认','摸牌结束 · 轮流询问反主或加固'];
 case 'final_bidding_continued':return ['继续确认',who+' 可继续反主抢庄，尚未拿底'];
 case 'kitty_picked_up':return ['拿底',who+' 拿起八张底牌'];
 case 'kitty_buried':return ['扣底',who+' 扣下八张牌'];
 case 'bottom_revealed':return ['翻底定主','无人亮主 · 八张底牌公开留底'];
 case 'play_started':return ['开牌',esc(nameOf(e.banker))+' 领出第一轮'];
 case 'trick_ended':return [`第 ${e.number} 轮`,esc(nameOf(e.winner))+' 赢得本轮',e.points+' 分'];
 case 'throw_failed':return ['甩牌失败',who+' 罚 '+e.penalty+' 分，强制出 '+e.cards.map(cardName).join(' ')];
 case 'round_ended':return ['本局结算','闲家 '+e.score+' 分 · '+(e.winner?'金队':'青队')+' 获胜'];
 case 'redeal':return ['重新发牌','抢庄局无人亮主，收牌重洗'];
 case 'server_error':return ['自动策略暂停',esc(e.message)];
 default:return null;
 }}
function renderFeed(){
 const s=snap.state,events=s.events.filter(e=>eventText(e)).slice(-22).reverse();
 $('eventFeed').innerHTML=events.map(e=>{const [head,text,pts]=eventText(e);return `<div class="event ${e.type==='throw_failed'?'penalty':''}"><div class="event-label">${head}${pts?`<span class="event-points">${pts}</span>`:''}</div>${text}</div>`;}).join('')||'<div class="empty-feed">等待第一手牌落下。</div>';
 const bid=s.bid;
 $('bidSummary').innerHTML=bid?`${esc(nameOf(bid.player))} · ${bid.cards.map(cardName).join(' ')}<br>亮主强度 ${bid.strength} / 4`:'尚未亮主 · 首次须用级牌';
 if((isMyTurn()||canBidNow())&&s.bid_options.length){
  $('bidSummary').innerHTML+='<div class="bid-chips">'+s.bid_options.map((a,i)=>`<button class="bid-chip" data-bid="${i}">${a.cards.map(cardName).join(' ')}</button>`).join('')+'</div>';
  for(const b of $('bidSummary').querySelectorAll('[data-bid]'))b.onclick=()=>{selected=new Set(s.bid_options[Number(b.dataset.bid)].cards);autoDeal=false;updateAutoDeal();renderHand();validateSelection();};
 }
}
function renderResult(){
 const s=snap.state,r=s.result;
 if(!['round_end','match_end'].includes(s.phase)||!r||!snap.settlement?.confirmed){$('resultOverlay').hidden=true;return;}
 if(closedResult===r.round)return;
 $('resultOverlay').hidden=false;
 $('resultTitle').textContent=s.phase==='match_end'?`${r.match_winner?'金队':'青队'}赢得整场比赛`:`${r.winner?'金队':'青队'}${r.winner===r.banker%2?'守庄成功':'上台坐庄'}`;
 $('resultScore').innerHTML=esc(r.score)+'<small>闲家最终得分</small>';
 $('resultBreakdown').innerHTML=`<span>抓分 ${r.captured}</span><span>底分 ${r.bottom}（×${r.multiplier}）</span><span>罚分 ${r.penalty>0?'+':''}${r.penalty}</span>`;
 $('resultLevels').innerHTML=`<span>青队 ${label(r.levels_before[0])} → <strong>${label(r.levels_after[0])}</strong></span><span>金队 ${label(r.levels_before[1])} → <strong>${label(r.levels_after[1])}</strong></span>`;
 $('resultKitty').innerHTML=r.kitty?.length?r.kitty.map(c=>cardHTML(c)).join(''):'';
 $('resultKitty').hidden=!r.kitty?.length;
 $('resultMessage').textContent=(r.downgrade?'触发 '+label(r.level)+' 扣底，庄家方退回 '+label(r.downgrade)+'。':'')+(r.mandatory_stop!=null?`必打 ${label(r.mandatory_stop)} 尚未坐庄打过，本次升级停在 ${label(r.mandatory_stop)}。`:'')+(s.phase==='match_end'?'A 级坐庄获胜，比赛结束。':`下一局由 ${nameOf(r.next_banker)} 坐庄。必打等级的完成记录永久保留。`);
 $('nextRoundButton').textContent=s.phase==='match_end'?'新建一场比赛 →':snap.host?'开始下一局 →':'等待房主开始下一局';
 $('nextRoundButton').disabled=s.phase!=='match_end'&&!snap.host;
 $('resultReplayLink').href=snap.replay_id?'/history.html?id='+encodeURIComponent(snap.replay_id):'/history.html';
 $('resultReplayLink').textContent=snap.replay_id?'回放这一局 ↗':'查看历史对局 ↗';
 $('historySaveNotice').textContent=snap.history_error|| (snap.replay_id?'本局已保存到历史对局':'');
 $('historySaveNotice').classList.toggle('save-error',!!snap.history_error);
}
function renderLobby(){
 if(!snap||mode!=='pvp')return;
 const seated=snap.seat!=null,waiting=!snap.started||!seated;
 $('lobbyOverlay').hidden=!waiting&&!roomManagerOpen;
 $('closeRoomSeats').hidden=waiting;
 $('lobbyTitle').textContent=snap.started?'房间席位':'好牌，从入座开始。';
 $('lobbyCode').textContent=snap.room;
 $('lobbyConfig').textContent='打 '+label(snap.config.initial_level)+(snap.config.auction?' · 首局抢庄':' · '+seatDirections[snap.config.banker]+'席坐庄');
 if(seated)tentativeSeat=snap.seat;
 else if(tentativeSeat!=null&&snap.players[tentativeSeat]?.occupied)tentativeSeat=null;
 const occupied=snap.players.filter(p=>p.occupied).length;
 $('lobbySeatHint').textContent=seated?(snap.started?`你在${seatDirections[snap.seat]}席 · 开局后座位固定`:`已确认${seatDirections[snap.seat]}席 · 可取消后重新选座`):tentativeSeat==null?'点击一个空席，按确定后才占用':`已预选${seatDirections[tentativeSeat]}席 · 尚未占用，按确定入座`;
 $('lobbySeats').innerHTML=snap.players.map(p=>{
  const mine=seated&&p.seat===snap.seat,chosen=p.seat===tentativeSeat;
  const disabled=seatBusy||p.occupied||seated;
  const aiControl=snap.host&&(!p.occupied||p.bot&&!snap.started);
  return `<div class="seat-choice ${['south','west','north','east'][p.seat]} ${p.occupied?'occupied':''} ${mine?'mine':''} ${chosen?'chosen':''} ${p.bot?'ai-seat':''}"><button class="seat-pick" data-seat-pick="${p.seat}" aria-label="${seatDirections[p.seat]}席${p.occupied?' · '+esc(p.name):' · 空席'}" aria-pressed="${chosen}" ${disabled?'disabled':''}><span class="seat-direction">${seatDirections[p.seat]}<small>${p.seat} 座 · ${p.seat%2?'金队':'青队'}</small></span><strong>${p.occupied?esc(p.name):chosen?'等待你确认':'空席'}</strong><small>${mine?'你已确认':p.bot?'本地 AI 策略':p.occupied?p.online?'已确认入座':'已确认 · 离线':'可以选择'}</small></button>${aiControl?`<button class="seat-ai-toggle" data-seat-ai="${p.seat}" data-enabled="${!p.bot}" ${seatBusy||!p.bot&&!seated?'disabled':''} aria-label="${seatDirections[p.seat]}席${p.bot?'移除':'添加'}AI">${p.bot?'移除 AI':'＋ AI 补位'}</button>`:''}</div>`;
 }).join('')+'<div class="seat-compass"><span>对家同队</span><small>四席 · 一桌</small></div>';
 for(const button of $('lobbySeats').querySelectorAll('[data-seat-pick]'))button.onclick=()=>chooseSeat(Number(button.dataset.seatPick));
 for(const button of $('lobbySeats').querySelectorAll('[data-seat-ai]'))button.onclick=()=>roomAction('/api/room/ai',{seat:Number(button.dataset.seatAi),enabled:button.dataset.enabled==='true'});
 $('confirmSeatButton').hidden=seated;$('confirmSeatButton').disabled=seatBusy||tentativeSeat==null;
 $('cancelSeatButton').hidden=!seated||snap.started;$('cancelSeatButton').disabled=seatBusy;
 $('fillAIButton').hidden=!snap.host;$('fillAIButton').disabled=seatBusy||!seated||occupied===4;
 $('startRoomButton').hidden=!snap.host||snap.started;$('startRoomButton').disabled=seatBusy||!snap.can_start;
 $('lobbyReady').textContent=snap.started?snap.paused?'有空席或玩家离线 · 等待补位或重连':'对局进行中':`${occupied} / 4 席已就绪${occupied===4?' · 等待房主开始':' · 可用 AI 补齐空席'}`;
 $('lobbyWaitingPeople').innerHTML=(snap.members||[]).filter(m=>m.seat==null).map(m=>`<span>${esc(m.name)}${m.self?' · 你':''}待选座</span>`).join('');
 $('lobbyHelp').textContent=snap.started?'断线保留原座位；真人主动离开的空席，可由新玩家确认入座或房主补 AI。':'选座预览仅自己可见，确认后才锁定。取消会释放席位；房主可用本地 AI 补齐空席，四席就绪后开始。';
}
function chooseSeat(seat){
 if(!snap||mode!=='pvp'||seatBusy||snap.seat!=null||snap.players[seat]?.occupied)return;
 tentativeSeat=seat;renderLobby();
}
async function roomAction(path,body={}){
 if(!snap||mode!=='pvp'||seatBusy)return;
 const scope=sessionScope();seatBusy=true;$('lobbyError').textContent='';renderLobby();
 try{const data=await api(path,body);if(!currentSession(scope))return;if(path==='/api/seat/cancel')tentativeSeat=null;if(path==='/api/room/start')roomManagerOpen=false;acceptSnapshot(data);rememberSeat();}
 catch(e){if(!currentSession(scope))return;$('lobbyError').textContent=e.message;try{const data=await api('/api/state');if(currentSession(scope))acceptSnapshot(data);}catch{}}
 finally{if(currentSession(scope)){seatBusy=false;renderLobby();}}
}
function acceptSnapshot(data){
 if(snap&&data.room===snap.room&&data.version<snap.version)return;
 const previous=snap;
 if(previous?.room!==data.room){tentativeSeat=null;roomManagerOpen=false;}
 snap=data;mode=snap.mode;
 notifyBids(previous);
 $('table').classList.toggle('self-view',mode==='self');
 $('setupOverlay').hidden=true;$('leaveButton').hidden=false;
 $('inviteButton').hidden=mode!=='pvp';
 $('roomSeatsButton').hidden=mode!=='pvp';
 $('connection').textContent=snap.mode==='pvp'?`房间 ${snap.room}`:'已连接 · 在线牌桌';
 $('modeTag').textContent={self:'自对弈 · 四家可见',pve:`人机对局 · ${snap.ai?.name||'AI'}`,pvp:'好友房间'}[mode];
 $('lobbyOverlay').hidden=snap.started;
 renderLobby();
 if(!snap.started||!snap.state)return;
 const s=snap.state;
 clearTimeout(biddingTimer);
 biddingEndAt=Date.now()+Math.max(0,s.bidding_window?.remaining_seconds||0)*1000;
 stopAutoDealOnDraw(previous);
 const nextKey=`${s.round}|${s.phase}|${s.trick_no}|${s.current_player}|${activeHand().join(',')}`;
 const scopeKey=`${snap.room}|${s.round}|${s.redeals}|${s.phase}|${snap.seat}`;
 if(mode!=='self'&&['playing','dealing'].includes(s.phase)){
  if(scopeKey!==selectionScopeKey)selected.clear();
  else selected=new Set([...selected].filter(c=>activeHand().includes(c)));
 }else if(nextKey!==handKey)selected.clear();
 selectionScopeKey=scopeKey;handKey=nextKey;
 if(isMyTurn()&&s.phase==='playing'&&s.trick.length&&s.forced_play?.length&&forcedSelectionKey!==nextKey){
  selected=new Set(s.forced_play);forcedSelectionKey=nextKey;
 }
 $('roundNumber').textContent=s.round;
 renderTeam('teamA',0);renderTeam('teamB',1);
 $('level').textContent=label(s.level);$('trump').textContent=s.phase==='dealing'&&!s.bid?'待亮主':s.trump?symbols[suits.indexOf(s.trump)]+' '+({S:'黑桃',H:'红桃',D:'方片',C:'草花'}[s.trump]):'无主';
 $('mobileLevel').textContent=label(s.level);$('mobileTrump').textContent=$('trump').textContent;$('mobileScore').textContent=s.score;
 $('score').textContent=s.score;$('captured').textContent=s.captured_score;$('bottom').textContent=s.bottom_score;$('penalty').textContent=(s.penalty_score>0?'+':'')+s.penalty_score;
 $('scoreFill').style.width=Math.min(100,Math.max(0,s.score))+'%';
 $('roomInfo').textContent=mode==='pvp'?`房间 ${snap.room} · 你在 ${snap.seat} 座`:mode==='self'?'教学牌桌 · 可操作四家':`你在 ${snap.seat} 座 · ${snap.seat%2?'金队':'青队'}`;
 const phaseText={dealing:'摸牌与亮主',burying:'拿底 · 扣八张',final_bidding:'拿底前确认主牌',countering:'扣底后反底',playing:'出牌阶段',round_end:'本局结算',match_end:'比赛结束'};
 $('phaseLabel').textContent=snap.paused?(snap.pause_reason==='policy_error'?'自动策略异常 · 已暂停':'牌桌已暂停 · 等待重连'):dealingComplete()?'摸牌结束 · 实时亮主':phaseText[s.phase];
 $('tableHint').textContent=s.phase==='dealing'?`已发 ${s.deal_count} / 100 张`:`第 ${s.trick_no+(['round_end','match_end'].includes(s.phase)?0:1)} 轮 · 打 ${label(s.level)}`;
 $('trickCount').textContent=s.trick_no+' 轮';
 $('tableStatus').textContent=snap.paused?(snap.pause_reason==='policy_error'?'自动策略已暂停，请检查服务端日志':'有玩家离线，原座位保留，重连后继续'):s.phase==='playing'?(s.trick.length?`${nameOf(s.current_player)} 须跟 ${s.trick[0].cards.length} 张`:`${nameOf(s.current_player)} 领出`):s.phase==='burying'?`${nameOf(s.current_player)} 选择八张扣底`:s.phase==='final_bidding'?`${nameOf(s.current_player)} 是否反主或加固？`:s.phase==='countering'?`${nameOf(s.current_player)} 是否反底？`:s.phase==='dealing'?`${nameOf(s.current_player)} 摸牌 · 可亮主`:'本局已结束';
 $('turnHint').textContent=['round_end','match_end'].includes(s.phase)?(snap.settlement?.confirmed?'本局结束 · 点击阶段标签查看结算':'本局结束 · 先核对底牌和计分'):dealingComplete()?'所有牌已摸完 · 六秒内仍可亮主 / 反主':snap.autoplay?(mode==='pve'?`${snap.ai?.name||'AI'}托管中，可随时暂停`:'随机策略托管中，可随时暂停'):canBidNow()?`${nameOf(s.current_player)} 正在摸牌 · 你可随时亮主 / 反主`:snap.bot_thinking?`${nameOf(s.current_player)} 正在思考…`:isMyTurn()?`${nameOf(s.current_player)} 行动 · Enter 提交`:`等待 ${nameOf(s.current_player)} 行动`;
 $('handTitle').textContent=(mode==='self'?`${s.current_player} 座 · ${nameOf(s.current_player)} 的手牌`:'你的手牌')+` · 本局打 ${label(s.level)}`;
 $('handSubtitle').textContent=s.phase==='burying'?'选择八张扣底':canBidNow()?'可随时预选并亮主／反主；当前定主者仅可同门加固':s.phase==='dealing'||s.phase==='final_bidding'||s.phase==='countering'?'选择级牌／王对亮牌，或不亮':!isMyTurn()&&mode!=='self'?'可提前预选，轮到你时确认出牌':'点击选择，再次点击取消';
 $('kittyArea').innerHTML=s.kitty_public&&s.kitty?.length?`公开底牌<div style="display:flex">${s.kitty.map(c=>cardHTML(c)).join('')}</div>`:s.phase==='burying'?'底牌已收入手中':`底牌 · 8 张${s.kitty_owner!=null?'已扣下':''}`;
 $('kittyArea').hidden=false;
 $('passButton').hidden=dealingComplete()||!isMyTurn()||!['dealing','final_bidding','countering'].includes(s.phase);
 $('passButton').textContent=s.phase==='dealing'?(s.realtime_bidding?'继续摸牌':'不亮，继续摸牌'):'不反';
 $('autoDealButton').hidden=dealingComplete()||!isMyTurn()||s.phase!=='dealing';updateAutoDeal();
 $('randomButton').hidden=dealingComplete()||!isMyTurn()||!['dealing','final_bidding','countering','burying','playing'].includes(s.phase)||mode==='pvp';
 $('autoplayButton').hidden=mode==='pvp'||['round_end','match_end'].includes(s.phase);
 $('autoplayButton').textContent=snap.autoplay?'暂停托管':'托管本局';$('autoplayButton').classList.toggle('is-active',snap.autoplay);
 renderDraw();renderPlayers();renderPlays();renderHand();renderFeed();renderSettlement();renderResult();validateSelection();scheduleAutoDeal();updateBiddingCountdown();
}
function validationUI(title,detail,error=false){$('validationTitle').textContent=title;$('validationDetail').textContent=detail;$('validation').classList.toggle('error',error);$('validation').querySelector('.validation-icon').textContent=error?'!':'·';}
function selectedAction(){return {type:snap.state.phase==='burying'?'bury':['dealing','final_bidding','countering'].includes(snap.state.phase)?'bid':'play',cards:[...selected]};}
async function validateSelection(){
 const ticket=++validationTicket;
 $('submitButton').disabled=true;
 if(!snap?.started||!snap.state)return;
 const s=snap.state;
 const ownBid=s.bid&&s.bid.player===(mode==='self'?s.current_player:snap.seat);
 $('submitButton').innerHTML=(s.phase==='burying'?'确认扣底':['dealing','final_bidding'].includes(s.phase)&&ownBid?'加固':s.phase==='dealing'&&s.realtime_bidding?'亮主 / 反主':['dealing','final_bidding','countering'].includes(s.phase)?'亮牌':['round_end','match_end'].includes(s.phase)?'查看结算':'出牌')+' <span>↗</span>';
 if(['round_end','match_end'].includes(s.phase)){validationUI('本局已结束',snap.settlement&&!snap.settlement.confirmed?'请先核对底牌，等待本局庄家确认':'点击阶段标签查看结算');return;}
 const realtime=canBidNow();
 if(dealingComplete()&&!snap.paused&&biddingEndAt<=Date.now()){validationUI('实时亮主已结束','等待服务器确定主牌和后续阶段');return;}
 if(!isMyTurn()&&!realtime){validationUI(snap.paused?'等待重新连接':['playing','dealing'].includes(s.phase)&&selected.size?`已预选 ${selected.size} 张 · 等待轮到你`:'等待牌友',snap.paused?'重连后保留原座位和手牌':s.phase==='playing'?`${nameOf(s.current_player)} 正在行动；轮到你时核对跟牌要求`:`${nameOf(s.current_player)} 正在行动`);return;}
 if(!selected.size){validationUI(s.phase==='burying'?'请选择八张扣底':'请选择牌',['dealing','final_bidding'].includes(s.phase)&&ownBid?(s.bid.strength===1?'当前主由你确定，仅可加固同花色级牌对子':'当前主由你确定，不能反自己；等待其他牌友反主'):s.phase==='dealing'?(s.bid?'反主须严格提高当前亮主强度':'首次亮主须为级牌，王只能反主'):s.phase==='final_bidding'?'拿底前反主须严格提高强度':s.phase==='countering'?'反主须严格高于当前强度':s.trick.length?leadRequirement()+'，优先同门同型':'可出单张、对子、拖拉机或同门甩牌');return;}
 validationUI(`已选 ${selected.size} 张`,'正在核对牌型…');
 try{
  const v=await api('/api/validate',{version:snap.version,round:s.round,redeals:s.redeals,context:{level:s.level,trump:s.trump},action:selectedAction()});
  if(ticket!==validationTicket)return;
  if((v.level!=null&&v.level!==s.level)||(realtime? v.phase!=null&&v.phase!==s.phase||v.round!=null&&v.round!==s.round||v.redeals!=null&&v.redeals!==s.redeals : (Object.hasOwn(v,'trump')&&v.trump!==s.trump)||(v.version!=null&&v.version!==snap.version))){
   validationUI('正在同步牌桌','正在核对本局级牌和首家领出');
   const data=await api('/api/state');if(ticket===validationTicket)acceptSnapshot(data);return;
  }
  if(v.valid){const forced=s.forced_play?.length===selected.size&&s.forced_play.every(c=>selected.has(c));validationUI(`${selected.size} 张 · ${v.message}`,realtime?'可立即提交，以服务器收到请求的先后为准':s.phase==='playing'&&!s.trick.length&&v.kind==='throw'?'甩牌尝试，提交后由裁判判定':forced?'唯一合法跟牌已预选 · 点击出牌确认'+(leadRequirement()?'；'+leadRequirement():''):leadRequirement()||'张数与跟牌要求符合桌规');$('submitButton').disabled=busy;}
  else validationUI(`已选 ${selected.size} 张 · 不可提交`,v.message+(s.phase==='playing'&&s.trick.length?'；'+leadRequirement():''),true);
 }catch(e){if(ticket!==validationTicket)return;validationUI('暂时无法提交',e.message,true);if(e.code==='STALE'){try{const data=await api('/api/state');if(ticket===validationTicket)acceptSnapshot(data);}catch{}}}
}
async function act(action){
 if(busy||!snap)return;
 const scope=sessionScope();
 busy=true;$('submitButton').disabled=true;$('passButton').disabled=true;
 // Do not fall back to the turn API when a local countdown just expired.
 const realtime=action.type==='bid'&&snap.state?.phase==='dealing'&&!!snap.state.realtime_bidding;
 const keepPreview=action.type==='pass'&&snap.state?.phase==='dealing'&&mode!=='self';
 try{const data=await api(realtime?'/api/bid':'/api/action',{version:snap.version,round:snap.state?.round,redeals:snap.state?.redeals,action});if(!currentSession(scope))return;if(!keepPreview)selected.clear();acceptSnapshot(data);}
 catch(e){if(!currentSession(scope))return;toast(e.message);if(e.code==='STALE'){try{const data=await api('/api/state');if(currentSession(scope))acceptSnapshot(data);}catch{}}}
 finally{if(currentSession(scope)){busy=false;$('passButton').disabled=false;if(snap?.state)renderHand();validateSelection();scheduleAutoDeal();}}
}
function updateAutoDeal(){$('autoDealButton').textContent=autoDeal?'暂停摸牌':'自动摸牌';$('autoDealButton').classList.toggle('is-active',autoDeal);}
function stopAutoDealOnDraw(previous){
 if(!autoDeal)return;
 const s=snap.state;
 if(s.phase!=='dealing'||dealingComplete()){autoDeal=false;clearTimeout(autoTimer);return;}
 if(!isMyTurn())return;
 const player=mode==='self'?s.current_player:snap.seat;
 const old=previous?.state;
 const hand=s.hands?.[player]||s.hand;
 const before=old?.hands?.[player]||(previous?.seat===player?old?.hand:null);
 const card=TractorDrawPreview.cardFor(s,player,before?new Set(before):null);
 const sameDraw=previous?.room===snap.room&&old?.phase==='dealing'&&old.round===s.round&&old.redeals===s.redeals&&old.deal_count===s.deal_count;
 if(card==null||sameDraw)return;
 if(rank(card)===s.level||rank(card)>=15&&hand.some(c=>c!==card&&face(c)===face(card))){
  autoDeal=false;clearTimeout(autoTimer);
  toast(rank(card)===s.level?'摸到级牌，自动摸牌已关闭，可考虑亮主或反主':'凑齐王对，自动摸牌已关闭，可考虑反主');
 }
}
async function confirmResult(){
 if(busy||!snap?.settlement?.can_confirm||snap.settlement.confirmed)return;
 const scope=sessionScope();busy=true;$('confirmResultButton').disabled=true;
 try{const data=await api('/api/confirm-result',{version:snap.version});if(currentSession(scope))acceptSnapshot(data);}
 catch(e){if(currentSession(scope)){toast(e.message);if(e.code==='STALE'){try{const data=await api('/api/state');if(currentSession(scope))acceptSnapshot(data);}catch{}}}}
 finally{if(currentSession(scope)){busy=false;$('confirmResultButton').disabled=false;}}
}
function scheduleAutoDeal(){
 clearTimeout(autoTimer);
 if(autoDeal&&snap?.state?.phase==='dealing'&&!dealingComplete()&&isMyTurn()&&!selected.size&&!busy){
  const scope=sessionScope(),draw=`${snap.room}|${snap.state.round}|${snap.state.redeals}|${snap.state.deal_count}`;
  autoTimer=setTimeout(()=>{if(currentSession(scope)&&autoDeal&&snap?.state?.phase==='dealing'&&!dealingComplete()&&isMyTurn()&&!selected.size&&!busy&&draw===`${snap.room}|${snap.state.round}|${snap.state.redeals}|${snap.state.deal_count}`)return act({type:'pass'});},650);
 }
}
async function poll(generation){
 while(token&&generation===pollGeneration){
  try{const data=await api(`/api/state?since=${snap?.version??-1}&wait=4`);if(generation!==pollGeneration)return;if(!snap||data.version!==snap.version)acceptSnapshot(data);$('connection').textContent=mode==='pvp'?`房间 ${data.room}`:'已连接 · 在线牌桌';}
  catch(e){if(generation!==pollGeneration)return;if(e.code==='SESSION'){forgetSeat(token);resetUI();toast('会话已结束，请重新入座');return;}$('connection').textContent='连接中…';await new Promise(r=>setTimeout(r,1500));}
 }
}
function resetUI(){
 pollGeneration++;validationTicket++;token=null;sessionStorage.removeItem('tractor_session');snap=null;selected.clear();busy=false;$('passButton').disabled=false;autoDeal=false;clearTimeout(autoTimer);
 clearTimeout(biddingTimer);biddingEndAt=0;
 handKey='';selectionScopeKey='';forcedSelectionKey='';closedResult=null;previousHands=[new Set(),new Set(),new Set(),new Set()];previousPlay='';
 tentativeSeat=null;seatBusy=false;roomManagerOpen=false;$('roomSeatsButton').hidden=true;$('lobbyError').textContent='';
 drawTurnKey='';visibleDraw=null;$('drawPreview').hidden=true;$('drawCard').innerHTML='';$('drawCard').dataset.draw='';
 clearTimeout(bidNoticeTimer);$('bidNotice').hidden=true;$('settlementPanel').hidden=$('settlementBottom').hidden=true;$('table').classList.toggle('reviewing',false);$('kittyArea').hidden=false;
 $('setupOverlay').hidden=false;$('lobbyOverlay').hidden=true;$('resultOverlay').hidden=true;$('leaveButton').hidden=true;$('inviteButton').hidden=true;$('inviteOverlay').hidden=true;
 for(const id of ['passButton','randomButton','autoDealButton','autoplayButton'])$(id).hidden=true;
 for(const id of ['playersLayer','playsLayer','hand','kittyArea','roomInfo'])$(id).innerHTML='';
 for(const id of ['score','captured','bottom','penalty'])$(id).textContent='0';
 $('roundNumber').textContent='—';$('level').textContent='2';$('trump').textContent='待亮主';$('scoreFill').style.width='0%';
 $('mobileLevel').textContent='2';$('mobileTrump').textContent='待亮主';$('mobileScore').textContent='0';updateResumeButton();
 $('connection').textContent='本地牌桌';$('modeTag').textContent='准备入座';$('phaseLabel').textContent='好牌，等你开局';$('tableHint').textContent='四人 · 两副牌 · 固定对家';
 $('tableStatus').textContent='选择玩法，入座开牌';$('handTitle').textContent='你的手牌';$('handSubtitle').textContent='点击选择，再次点击取消';
 $('turnHint').textContent='支持本地教学、PVE 和四人房间';$('trickCount').textContent='0 轮';$('bidSummary').textContent='亮主信息将在这里显示';$('eventFeed').innerHTML='<div class="empty-feed">等待第一手牌落下。</div>';
 $('submitButton').disabled=true;$('submitButton').innerHTML='出牌 <span>↗</span>';validationUI('等待开局','选择玩法，开始你们的升级比赛');renderTeam('teamA',0);renderTeam('teamB',1);chooseMode(mode);
}
function updateBanker(){const team=Number($('teamInput').value);$('bankerInput').innerHTML=[team,team+2].map(s=>`<option value="${s}">${s} 座（${['南','西','北','东'][s]}）</option>`).join('');}
function chooseMode(value){mode=value;for(const b of document.querySelectorAll('[data-mode]'))b.classList.toggle('active',b.dataset.mode===mode);$('roomField').hidden=mode!=='pvp';$('humanSeatField').hidden=mode!=='pve';$('setupForm').querySelector('[type=submit]').innerHTML=(mode==='pvp'?'进入房间':'入座开牌')+' <span>→</span>';}
$('levelInput').innerHTML=Array.from({length:13},(_,i)=>`<option value="${i+2}">${label(i+2)}</option>`).join('');updateBanker();
for(const b of document.querySelectorAll('[data-mode]'))b.onclick=()=>chooseMode(b.dataset.mode);
$('auctionInput').onchange=()=>$('bankerOptions').hidden=$('auctionInput').checked;
$('teamInput').onchange=updateBanker;
$('setupForm').onsubmit=async e=>{
 e.preventDefault();if(e.target.querySelector('[type=submit]').disabled)return;$('setupError').textContent='';const button=e.target.querySelector('[type=submit]');button.disabled=true;
 pollGeneration++;const scope=sessionScope();
 try{const data=await api('/api/session',{mode,name:$('nameInput').value,room:$('roomInput').value,seat:Number($('humanSeatInput').value),config:{initial_level:Number($('levelInput').value),auction:$('auctionInput').checked,banker:Number($('bankerInput').value)}});if(!currentSession(scope))return;token=data.token;sessionStorage.setItem('tractor_session',token);snap=null;closedResult=null;acceptSnapshot(data);rememberSeat();$('resumeButton').hidden=true;poll(++pollGeneration);}
 catch(err){if(currentSession(scope)){$('setupError').textContent=err.message;button.disabled=false;if(token)poll(++pollGeneration);}}finally{button.disabled=false;}
};
$('clearButton').onclick=()=>{selected.clear();syncSelection();validateSelection();};
$('submitButton').onclick=()=>act(selectedAction());
$('confirmResultButton').onclick=confirmResult;
$('confirmSeatButton').onclick=()=>roomAction('/api/seat/confirm',{seat:tentativeSeat});
$('cancelSeatButton').onclick=()=>roomAction('/api/seat/cancel');
$('fillAIButton').onclick=()=>roomAction('/api/room/ai',{enabled:true});
$('startRoomButton').onclick=()=>roomAction('/api/room/start');
$('roomSeatsButton').onclick=()=>{roomManagerOpen=true;renderLobby();};
$('closeRoomSeats').onclick=()=>{roomManagerOpen=false;renderLobby();};
$('passButton').onclick=()=>act({type:'pass'});
$('randomButton').onclick=()=>act({type:'random'});
$('autoDealButton').onclick=()=>{autoDeal=!autoDeal;selected.clear();renderHand();validateSelection();updateAutoDeal();scheduleAutoDeal();};
$('autoplayButton').onclick=async()=>{if(!snap)return;const scope=sessionScope();try{const data=await api('/api/autoplay',{enabled:!snap.autoplay});if(currentSession(scope))acceptSnapshot(data);}catch(e){if(currentSession(scope))toast(e.message);}};
async function leaveSession(){
 if(!token)return;if($('leaveButton').disabled)return;
 $('leaveButton').disabled=$('leaveLobbyButton').disabled=true;pollGeneration++;validationTicket++;autoDeal=false;clearTimeout(autoTimer);const scope=sessionScope();
 try{await api('/api/leave',{});if(!currentSession(scope))return;forgetSeat(scope.token);resetUI();}
 catch(e){if(!currentSession(scope))return;if(e.code==='SESSION'){forgetSeat(scope.token);resetUI();}else{busy=false;toast('尚未离开：'+e.message);validateSelection();poll(++pollGeneration);}}
 finally{$('leaveButton').disabled=$('leaveLobbyButton').disabled=false;}
}
$('leaveLobbyButton').onclick=$('leaveButton').onclick=leaveSession;
$('copyRoomButton').onclick=()=>copyText(snap.room,'房间号已复制：'+snap.room);
$('copyInviteButton').onclick=$('inviteButton').onclick=showInvite;
$('closeInvite').onclick=()=>$('inviteOverlay').hidden=true;
$('copyInviteURL').onclick=()=>copyText($('inviteURL').value,'邀请链接已复制，发给三位牌友即可');
$('inviteURL').onclick=()=>$('inviteURL').select();
$('resumeButton').onclick=async()=>{if(!remembered||$('resumeButton').disabled)return;const seat=remembered;resetUI();token=seat.token;const scope=sessionScope();$('resumeButton').disabled=true;try{const data=await api('/api/state');if(!currentSession(scope))return;acceptSnapshot(data);sessionStorage.setItem('tractor_session',token);rememberSeat();$('resumeButton').hidden=true;poll(++pollGeneration);}catch(e){if(!currentSession(scope))return;if(e.code==='SESSION')forgetSeat(scope.token);resetUI();toast(e.message);}finally{$('resumeButton').disabled=false;}};
$('closeResult').onclick=()=>{closedResult=snap.state.result.round;$('resultOverlay').hidden=true;};
$('phaseLabel').onclick=()=>{if(['round_end','match_end'].includes(snap?.state?.phase)){closedResult=null;renderResult();}};
$('nextRoundButton').onclick=()=>{if(snap.state.phase==='match_end')return leaveSession();closedResult=null;act({type:'next_round'});};
$('rulesButton').onclick=async()=>{$('rulesOverlay').hidden=false;try{$('rulesText').textContent=await (await fetch('/rules')).text();}catch{$('rulesText').textContent='规则加载失败，请刷新重试';}};
$('closeRules').onclick=()=>$('rulesOverlay').hidden=true;
document.addEventListener('keydown',e=>{if(['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName))return;if(e.key==='Escape'){$('rulesOverlay').hidden=true;$('inviteOverlay').hidden=true;selected.clear();if(snap?.started&&snap.state){roomManagerOpen=false;renderLobby();syncSelection();validateSelection();}return;}if(!['rulesOverlay','inviteOverlay','resultOverlay','setupOverlay','lobbyOverlay'].every(id=>$(id).hidden))return;if(e.key==='Enter'&&!$('submitButton').disabled){e.preventDefault();$('submitButton').click();}});
window.addEventListener('resize',()=>{if(snap?.started&&snap.state){renderDraw();renderHand();renderPlays();}});
if(typeof ResizeObserver==='function')new ResizeObserver(()=>{if(snap?.started&&snap.state)layoutHand();}).observe($('hand'));
renderTeam('teamA',0);renderTeam('teamB',1);
api('/api/site').then(site=>publicURL=site.public_url||'').catch(()=>{});
if(invitedRoom&&/^[A-Za-z0-9_-]{3,24}$/.test(invitedRoom)){$('roomInput').value=invitedRoom;chooseMode('pvp');$('inviteNotice').textContent='你收到房间 '+invitedRoom+' 的邀请，填写名字并确认座位即可入座。';$('inviteNotice').hidden=false;if(remembered?.room!==invitedRoom)token=null;}
else if(!['localhost','127.0.0.1','::1'].includes(location.hostname))chooseMode('pvp');
if(remembered&&(!invitedRoom||remembered.room===invitedRoom)){$('resumeButton').hidden=false;$('resumeButton').textContent='回到上次牌桌'+(remembered.mode==='pvp'?' · '+remembered.room:'');$('nameInput').value=remembered.name||'牌友';}
if(token){const scope=sessionScope();api('/api/state').then(data=>{if(!currentSession(scope))return;acceptSnapshot(data);rememberSeat();poll(++pollGeneration);}).catch(e=>{if(!currentSession(scope))return;if(e.code==='SESSION')forgetSeat(scope.token);resetUI();if(e.code!=='SESSION')toast('暂时无法重连，可点击回到上次牌桌');});}

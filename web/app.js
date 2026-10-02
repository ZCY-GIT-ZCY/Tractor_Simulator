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
let handKey='', forcedSelectionKey='', validationTicket=0, pollGeneration=0, closedResult=null, autoDeal=false, autoTimer=null, toastTimer;
let previousHands=[new Set(),new Set(),new Set(),new Set()], previousPlay='';
let drawTurnKey='', visibleDraw=null;
let publicURL='', remembered=null;
const invitedRoom=new URLSearchParams(location.search).get('room');
try{remembered=JSON.parse(localStorage.getItem('tractor_resume')||'null');}catch{}
if(!remembered||typeof remembered.token!=='string'||typeof remembered.room!=='string')remembered=null;
function updateResumeButton(){const available=remembered&&(!invitedRoom||remembered.room===invitedRoom);$('resumeButton').hidden=!available;$('resumeButton').textContent='回到上次牌桌'+(remembered?.mode==='pvp'?' · '+remembered.room:'');}
function rememberSeat(){remembered={token,room:snap.room,mode:snap.mode,name:nameOf(snap.seat)};try{localStorage.setItem('tractor_resume',JSON.stringify(remembered));}catch{}}
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
function nameOf(seat){return snap?.players[seat]?.name||`${seat} 座`;}
function position(seat){return ['south','west','north','east'][(seat-(snap?.mode==='self'?0:snap?.seat||0)+4)%4];}
function isMyTurn(){return !!snap?.started&&!snap.paused&&snap.controlled.includes(snap.state.current_player);}
function isTrump(id){const s=snap?.state;return s&&(rank(id)>14||rank(id)===s.level||(s.trump&&suit(id)===s.trump));}
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
  if(visible&&(pos!=='south'||snap.mode==='self')){
   hand=['north','south'].includes(pos)?`<div class="mini-hand">${visible.map(c=>cardHTML(c,previousHands[p.seat].has(c)?'':'new')).join('')}</div>`:
     `<div class="side-hand">${visible.map(c=>`<div class="card-chip ${suit(c)==='H'||suit(c)==='D'?'red':''}" title="${cardName(c)}">${label(rank(c))}<span>${rank(c)>14?'✦':symbols[suits.indexOf(suit(c))]}</span></div>`).join('')}</div>`;
  }else if(pos!=='south'&&s.hand_counts[p.seat])hand='<div class="hidden-stack"></div>';
  if(visible)previousHands[p.seat]=new Set(visible);
  return `<div class="seat ${pos} ${active?'active':''}"><div class="player-badge"><div class="avatar">${p.seat}</div><div class="player-text"><strong>${esc(p.name)}${banker===p.seat?'<b class="banker-badge">庄</b>':''}</strong><small>${s.hand_counts[p.seat]} 张 · ${p.seat%2?'金队':'青队'}${!p.online?' · 离线':''}</small></div></div>${hand}</div>`;
 }).join('');
}
function renderPlays(){
 const s=snap.state,show=s.trick.length?s.trick:s.last_trick?.plays||[];
 const current=JSON.stringify(show),fresh=current!==previousPlay&&s.trick.length;
 $('playsLayer').innerHTML=show.map(p=>{
  const pos=position(p.player),width=$('table').clientWidth,cardWidth=innerWidth<=760?32:44;
  const step=Math.min(cardWidth-18,Math.max(5,(width*(['north','south'].includes(pos)?0.72:0.36)-cardWidth)/Math.max(1,p.cards.length-1)));
  return `<div class="play ${pos} ${fresh&&p===show[show.length-1]?'fresh':''} ${!s.trick.length&&s.last_trick?.winner===p.player?'winner':''}" style="--play-overlap:${step-cardWidth}px">${p.cards.map(c=>cardHTML(c)).join('')}</div>`;
 }).join('');
 previousPlay=current;
}
function activeHand(){return displayHand(snap.mode==='self'?snap.state.hands[snap.state.current_player]:snap.state.hand);}
function renderHand(){
 const h=activeHand(),enabled=isMyTurn()&&!['round_end','match_end'].includes(snap.state.phase);
 $('hand').className='full-hand '+(!enabled?'disabled':'');
 $('hand').style.setProperty('--overlap',`${-Math.max(0,61-Math.max(22,($('hand').clientWidth-60)/Math.max(1,h.length-1)))}px`);
 $('hand').innerHTML=h.length?h.map(c=>cardHTML(c,selected.has(c)?'selected':'')).join(''):'<span class="small-empty">'+(['round_end','match_end'].includes(snap.state.phase)?'本局手牌已出完':'正在摸牌，稍等片刻')+'</span>';
 if(enabled){for(const element of $('hand').querySelectorAll('[data-card]')){
  element.role='button';element.tabIndex=0;element.setAttribute('aria-pressed',selected.has(Number(element.dataset.card)));
  const toggle=()=>{autoDeal=false;updateAutoDeal();const c=Number(element.dataset.card);selected.has(c)?selected.delete(c):selected.add(c);syncSelection();validateSelection();};
  element.addEventListener('click',toggle);element.addEventListener('keydown',e=>{if(e.key===' '||e.key==='Enter'){e.preventDefault();e.stopPropagation();toggle();}});
 }}
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
 if(isMyTurn()&&s.bid_options.length){
  $('bidSummary').innerHTML+='<div class="bid-chips">'+s.bid_options.map((a,i)=>`<button class="bid-chip" data-bid="${i}">${a.cards.map(cardName).join(' ')}</button>`).join('')+'</div>';
  for(const b of $('bidSummary').querySelectorAll('[data-bid]'))b.onclick=()=>{selected=new Set(s.bid_options[Number(b.dataset.bid)].cards);autoDeal=false;updateAutoDeal();renderHand();validateSelection();};
 }
}
function renderResult(){
 const s=snap.state,r=s.result;
 if(!['round_end','match_end'].includes(s.phase)||!r){$('resultOverlay').hidden=true;return;}
 if(closedResult===r.round)return;
 $('resultOverlay').hidden=false;
 $('resultTitle').textContent=s.phase==='match_end'?`${r.match_winner?'金队':'青队'}赢得整场比赛`:`${r.winner?'金队':'青队'}${r.winner===r.banker%2?'守庄成功':'上台坐庄'}`;
 $('resultScore').innerHTML=esc(r.score)+'<small>闲家最终得分</small>';
 $('resultBreakdown').innerHTML=`<span>抓分 ${r.captured}</span><span>底分 ${r.bottom}（×${r.multiplier}）</span><span>罚分 ${r.penalty>0?'+':''}${r.penalty}</span>`;
 $('resultLevels').innerHTML=`<span>青队 ${label(r.levels_before[0])} → <strong>${label(r.levels_after[0])}</strong></span><span>金队 ${label(r.levels_before[1])} → <strong>${label(r.levels_after[1])}</strong></span>`;
 $('resultKitty').innerHTML=r.kitty?.length?r.kitty.map(c=>cardHTML(c)).join(''):'';
 $('resultKitty').hidden=!r.kitty?.length;
 $('resultMessage').textContent=(r.downgrade?'触发 '+label(r.level)+' 扣底，庄家方退回 '+label(r.downgrade)+'。':'')+(s.phase==='match_end'?'A 级坐庄获胜，比赛结束。':`下一局由 ${nameOf(r.next_banker)} 坐庄。必打等级的完成记录永久保留。`);
 $('nextRoundButton').textContent=s.phase==='match_end'?'新建一场比赛 →':snap.host?'开始下一局 →':'等待房主开始下一局';
 $('nextRoundButton').disabled=s.phase!=='match_end'&&!snap.host;
}
function acceptSnapshot(data){
 if(snap&&data.room===snap.room&&data.version<snap.version)return;
 snap=data;mode=snap.mode;
 $('table').classList.toggle('self-view',mode==='self');
 $('setupOverlay').hidden=true;$('leaveButton').hidden=false;
 $('inviteButton').hidden=mode!=='pvp';
 $('connection').textContent=snap.mode==='pvp'?`房间 ${snap.room}`:'已连接 · 在线牌桌';
 $('modeTag').textContent={self:'自对弈 · 四家可见',pve:'人机对局',pvp:'好友房间'}[mode];
 $('lobbyOverlay').hidden=snap.started;
 if(!snap.started){
  $('lobbyCode').textContent=snap.room;$('lobbyConfig').textContent='打 '+label(snap.config.initial_level)+(snap.config.auction?' · 首局抢庄':' · '+snap.config.banker+' 座坐庄');
  $('lobbySeats').innerHTML=snap.players.map(p=>`<div class="lobby-seat ${p.occupied?'filled':''}"><div class="avatar">${p.seat}</div>${p.occupied?esc(p.name):'等待入座'}<br><small>${p.seat%2?'金队':'青队'}${p.seat===snap.seat?' · 你':''}</small></div>`).join('');return;
 }
 const s=snap.state;
 const nextKey=`${s.round}|${s.phase}|${s.trick_no}|${s.current_player}|${activeHand().join(',')}`;
 if(nextKey!==handKey){selected.clear();handKey=nextKey;}
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
 $('phaseLabel').textContent=snap.paused?(snap.pause_reason==='policy_error'?'自动策略异常 · 已暂停':'牌桌已暂停 · 等待重连'):phaseText[s.phase];
 $('tableHint').textContent=s.phase==='dealing'?`已发 ${s.deal_count} / 100 张`:`第 ${s.trick_no+(['round_end','match_end'].includes(s.phase)?0:1)} 轮 · 打 ${label(s.level)}`;
 $('trickCount').textContent=s.trick_no+' 轮';
 $('tableStatus').textContent=snap.paused?(snap.pause_reason==='policy_error'?'自动策略已暂停，请检查服务端日志':'有玩家离线，原座位保留，重连后继续'):s.phase==='playing'?(s.trick.length?`${nameOf(s.current_player)} 须跟 ${s.trick[0].cards.length} 张`:`${nameOf(s.current_player)} 领出`):s.phase==='burying'?`${nameOf(s.current_player)} 选择八张扣底`:s.phase==='final_bidding'?`${nameOf(s.current_player)} 是否反主或加固？`:s.phase==='countering'?`${nameOf(s.current_player)} 是否反底？`:s.phase==='dealing'?`${nameOf(s.current_player)} 摸牌 · 可亮主`:'本局已结束';
 $('turnHint').textContent=['round_end','match_end'].includes(s.phase)?'本局结束 · 点击阶段标签查看结算':snap.autoplay?'随机策略托管中，可随时暂停':isMyTurn()?`${nameOf(s.current_player)} 行动 · Enter 提交`:`等待 ${nameOf(s.current_player)} 行动`;
 $('handTitle').textContent=mode==='self'?`${s.current_player} 座 · ${nameOf(s.current_player)} 的手牌`:'你的手牌';
 $('handSubtitle').textContent=s.phase==='burying'?'选择八张扣底':s.phase==='dealing'||s.phase==='final_bidding'||s.phase==='countering'?'选择级牌／王对亮牌，或不亮':'点击选择，再次点击取消';
 $('kittyArea').innerHTML=s.kitty_public&&s.kitty?.length?`公开底牌<div style="display:flex">${s.kitty.map(c=>cardHTML(c)).join('')}</div>`:s.phase==='burying'?'底牌已收入手中':`底牌 · 8 张${s.kitty_owner!=null?'已扣下':''}`;
 $('passButton').hidden=!isMyTurn()||!['dealing','final_bidding','countering'].includes(s.phase);
 $('passButton').textContent=s.phase==='dealing'?'不亮，继续摸牌':'不反';
 $('autoDealButton').hidden=!isMyTurn()||s.phase!=='dealing';updateAutoDeal();
 $('randomButton').hidden=!isMyTurn()||!['dealing','final_bidding','countering','burying','playing'].includes(s.phase)||mode==='pvp';
 $('autoplayButton').hidden=mode==='pvp'||['round_end','match_end'].includes(s.phase);
 $('autoplayButton').textContent=snap.autoplay?'暂停托管':'托管本局';$('autoplayButton').classList.toggle('is-active',snap.autoplay);
 renderDraw();renderPlayers();renderPlays();renderHand();renderFeed();renderResult();validateSelection();scheduleAutoDeal();
}
function validationUI(title,detail,error=false){$('validationTitle').textContent=title;$('validationDetail').textContent=detail;$('validation').classList.toggle('error',error);$('validation').querySelector('.validation-icon').textContent=error?'!':'·';}
function selectedAction(){return {type:snap.state.phase==='burying'?'bury':['dealing','final_bidding','countering'].includes(snap.state.phase)?'bid':'play',cards:[...selected]};}
async function validateSelection(){
 const ticket=++validationTicket;
 $('submitButton').disabled=true;
 if(!snap?.started)return;
 const s=snap.state;
 $('submitButton').innerHTML=(s.phase==='burying'?'确认扣底':['dealing','final_bidding','countering'].includes(s.phase)?'亮牌':s.phase==='round_end'?'查看结算':'出牌')+' <span>↗</span>';
 if(!isMyTurn()){validationUI(snap.paused?'等待重新连接':'等待牌友',snap.paused?'重连后保留原座位和手牌':`${nameOf(s.current_player)} 正在行动`);return;}
 if(['round_end','match_end'].includes(s.phase)){validationUI('本局已结束','点击出牌区可查看上一轮；下一局由房主开始');return;}
 if(!selected.size){validationUI(s.phase==='burying'?'请选择八张扣底':'请选择牌',s.phase==='dealing'?(s.bid?'可反主或本人加固，须严格提高亮主强度':'首次亮主须为级牌，王只能反主'):s.phase==='final_bidding'?'拿底前可反主或本人加固，须严格提高强度':s.phase==='countering'?'反主须严格高于当前强度':s.trick.length?`本轮须跟 ${s.trick[0].cards.length} 张，优先同门同型`:'可出单张、对子、拖拉机或同门甩牌');return;}
 validationUI(`已选 ${selected.size} 张`,'正在核对牌型…');
 try{
  const v=await api('/api/validate',{action:selectedAction()});
  if(ticket!==validationTicket)return;
  if(v.valid){const forced=s.forced_play?.length===selected.size&&s.forced_play.every(c=>selected.has(c));validationUI(`${selected.size} 张 · ${v.message}`,v.kind==='throw'?'甩牌尝试，提交后由裁判判定':forced?'唯一合法跟牌已预选 · 点击出牌确认':'张数与跟牌要求符合桌规');$('submitButton').disabled=busy;}
  else validationUI(`已选 ${selected.size} 张 · 不可提交`,v.message,true);
 }catch(e){if(ticket===validationTicket)validationUI('暂时无法提交',e.message,true);}
}
async function act(action){
 if(busy||!snap)return;
 const scope=sessionScope();
 busy=true;$('submitButton').disabled=true;$('passButton').disabled=true;
 try{const data=await api('/api/action',{version:snap.version,action});if(!currentSession(scope))return;selected.clear();acceptSnapshot(data);}
 catch(e){if(!currentSession(scope))return;toast(e.message);if(e.code==='STALE'){try{const data=await api('/api/state');if(currentSession(scope))acceptSnapshot(data);}catch{}}}
 finally{if(currentSession(scope)){busy=false;$('passButton').disabled=false;validateSelection();scheduleAutoDeal();}}
}
function updateAutoDeal(){$('autoDealButton').textContent=autoDeal?'暂停摸牌':'自动摸牌';$('autoDealButton').classList.toggle('is-active',autoDeal);}
function scheduleAutoDeal(){clearTimeout(autoTimer);if(autoDeal&&snap?.state?.phase==='dealing'&&isMyTurn()&&!selected.size&&!busy)autoTimer=setTimeout(()=>act({type:'pass'}),650);}
async function poll(generation){
 while(token&&generation===pollGeneration){
  try{const data=await api(`/api/state?since=${snap?.version??-1}&wait=4`);if(generation!==pollGeneration)return;if(!snap||data.version!==snap.version)acceptSnapshot(data);$('connection').textContent=mode==='pvp'?`房间 ${data.room}`:'已连接 · 在线牌桌';}
  catch(e){if(generation!==pollGeneration)return;if(e.code==='SESSION'){forgetSeat(token);resetUI();toast('会话已结束，请重新入座');return;}$('connection').textContent='连接中…';await new Promise(r=>setTimeout(r,1500));}
 }
}
function resetUI(){
 pollGeneration++;validationTicket++;token=null;sessionStorage.removeItem('tractor_session');snap=null;selected.clear();busy=false;$('passButton').disabled=false;autoDeal=false;clearTimeout(autoTimer);
 handKey='';forcedSelectionKey='';closedResult=null;previousHands=[new Set(),new Set(),new Set(),new Set()];previousPlay='';
 drawTurnKey='';visibleDraw=null;$('drawPreview').hidden=true;$('drawCard').innerHTML='';$('drawCard').dataset.draw='';
 $('setupOverlay').hidden=false;$('lobbyOverlay').hidden=true;$('resultOverlay').hidden=true;$('leaveButton').hidden=true;$('inviteButton').hidden=true;$('inviteOverlay').hidden=true;
 for(const id of ['passButton','randomButton','autoDealButton','autoplayButton'])$(id).hidden=true;
 for(const id of ['playersLayer','playsLayer','hand','kittyArea','roomInfo'])$(id).innerHTML='';
 for(const id of ['score','captured','bottom','penalty'])$(id).textContent='0';
 $('roundNumber').textContent='—';$('level').textContent='2';$('trump').textContent='待亮主';$('scoreFill').style.width='0%';
 $('mobileLevel').textContent='2';$('mobileTrump').textContent='待亮主';$('mobileScore').textContent='0';updateResumeButton();
 $('connection').textContent='本地牌桌';$('modeTag').textContent='准备入座';$('phaseLabel').textContent='好牌，等你开局';$('tableHint').textContent='四人 · 两副牌 · 固定对家';
 $('tableStatus').textContent='选择玩法，入座开牌';$('handTitle').textContent='你的手牌';$('handSubtitle').textContent='点击选择，再次点击取消';
 $('turnHint').textContent='支持本地教学、PVE 和四人房间';$('trickCount').textContent='0 轮';$('bidSummary').textContent='亮主信息将在这里显示';$('eventFeed').innerHTML='<div class="empty-feed">等待第一手牌落下。</div>';
 $('submitButton').disabled=true;$('submitButton').innerHTML='出牌 <span>↗</span>';validationUI('等待开局','选择玩法，开始你们的升级比赛');renderTeam('teamA',0);renderTeam('teamB',1);
}
function updateBanker(){const team=Number($('teamInput').value);$('bankerInput').innerHTML=[team,team+2].map(s=>`<option value="${s}">${s} 座（${['南','西','北','东'][s]}）</option>`).join('');}
function chooseMode(value){mode=value;for(const b of document.querySelectorAll('[data-mode]'))b.classList.toggle('active',b.dataset.mode===mode);$('roomField').hidden=mode!=='pvp';$('humanSeatField').hidden=mode!=='pve';}
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
document.addEventListener('keydown',e=>{if(['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName))return;if(e.key==='Escape'){$('rulesOverlay').hidden=true;$('inviteOverlay').hidden=true;selected.clear();if(snap?.started){syncSelection();validateSelection();}return;}if(!['rulesOverlay','inviteOverlay','resultOverlay','setupOverlay','lobbyOverlay'].every(id=>$(id).hidden))return;if(e.key==='Enter'&&!$('submitButton').disabled){e.preventDefault();$('submitButton').click();}});
window.addEventListener('resize',()=>{if(snap?.started){renderDraw();renderHand();renderPlays();}});
renderTeam('teamA',0);renderTeam('teamB',1);
api('/api/site').then(site=>publicURL=site.public_url||'').catch(()=>{});
if(invitedRoom&&/^[A-Za-z0-9_-]{3,24}$/.test(invitedRoom)){$('roomInput').value=invitedRoom;chooseMode('pvp');$('inviteNotice').textContent='你收到房间 '+invitedRoom+' 的邀请，填写名字即可入座。';$('inviteNotice').hidden=false;if(remembered?.room!==invitedRoom)token=null;}
else if(!['localhost','127.0.0.1','::1'].includes(location.hostname))chooseMode('pvp');
if(remembered&&(!invitedRoom||remembered.room===invitedRoom)){$('resumeButton').hidden=false;$('resumeButton').textContent='回到上次牌桌'+(remembered.mode==='pvp'?' · '+remembered.room:'');$('nameInput').value=remembered.name||'牌友';}
if(token){const scope=sessionScope();api('/api/state').then(data=>{if(!currentSession(scope))return;acceptSnapshot(data);rememberSeat();poll(++pollGeneration);}).catch(e=>{if(!currentSession(scope))return;if(e.code==='SESSION')forgetSeat(scope.token);resetUI();if(e.code!=='SESSION')toast('暂时无法重连，可点击回到上次牌桌');});}

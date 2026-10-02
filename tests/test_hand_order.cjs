const {test}=require('node:test');
const assert=require('node:assert/strict');
const {sort}=require('../web/hand-order.js');
const suits=['S','H','D','C'];
const c=(s,r)=>suits.indexOf(s)*13+r-2;
const suit=id=>id%54<52?suits[Math.floor((id%54)/13)]:null;
const rank=id=>id%54<52?(id%54)%13+2:(id%54)-37;
const red=s=>s==='H'||s==='D';

test('all trump choices keep black/red suit blocks alternating and trump at right',()=>{
 const cards=['D','C','H','S'].map(s=>c(s,3));
 const expected=new Map([[null,['S','H','C','D']],['S',['H','C','D','S']],
  ['H',['S','D','C','H']],['D',['S','H','C','D']],['C',['H','S','D','C']]]);
 for(const [trump,order] of expected)assert.deepEqual(sort(cards,6,trump).map(suit),order);
});

test('emptying a suit automatically rearranges the surviving suit blocks',()=>{
 const hand=['S','D','C','H'].map(s=>c(s,3));
 assert.deepEqual(sort(hand,6,'H').map(suit),['S','D','C','H']);
 const after=hand.filter(card=>suit(card)!=='S');
 assert.deepEqual(sort(after,6,'H').map(suit),['D','C','H']);
 // Without ordinary hearts there is no phantom trump-color boundary.
 assert.deepEqual(sort(after.filter(card=>suit(card)!=='H').concat(c('H',6)),6,'H').map(suit),['C','D','H']);
});

test('ordinary suit trumps precede all level cards and jokers',()=>{
 const constants=[c('S',6),c('D',6),c('C',6),c('H',6),52,106,53,107];
 const hand=[...constants,c('H',14),c('H',3),c('S',4),c('D',8),c('C',9)].reverse();
 const ordered=sort(hand,6,'H');
 assert.deepEqual(ordered.slice(-8),[c('S',6),c('C',6),c('D',6),c('H',6),52,106,53,107]);
 assert.deepEqual(ordered.slice(-10,-8),[c('H',3),c('H',14)]);
 const noTrump=sort(hand,6,null);
 assert.ok(noTrump.slice(-8).every(card=>rank(card)===6||rank(card)>14));
});

test('sort preserves entities and input, keeps duplicate faces adjacent, and is stable',()=>{
 const hand=[c('H',8)+54,c('S',4),c('H',8),c('S',3)+54,c('S',3),c('D',6),53];
 const before=[...hand],ordered=sort(hand,6,'H');
 assert.deepEqual(hand,before);
 assert.deepEqual([...ordered].sort((a,b)=>a-b),[...hand].sort((a,b)=>a-b));
 assert.equal(ordered.indexOf(c('H',8)+54),ordered.indexOf(c('H',8))+1);
 assert.equal(ordered.indexOf(c('S',3)+54),ordered.indexOf(c('S',3))+1);
 assert.deepEqual(sort(ordered,6,'H'),ordered);
});

test('all remaining-suit subsets alternate whenever their color counts allow it',()=>{
 for(let mask=0;mask<16;mask++)for(const trump of [null,...suits]){
  const present=suits.filter((_,i)=>mask&(1<<i));
  const hand=present.map(s=>c(s,3)),ordered=sort(hand,6,trump).map(suit);
  if(trump&&present.includes(trump))assert.equal(ordered.at(-1),trump);
  const reds=present.filter(red).length,blacks=present.length-reds;
  const fixed=trump&&present.includes(trump)?trump:null;
  const canAlternate=Math.abs(reds-blacks)<=1&&(!fixed||reds===blacks||red(fixed)===(reds>blacks));
  if(canAlternate)for(let i=1;i<ordered.length;i++)assert.notEqual(red(ordered[i]),red(ordered[i-1]));
 }
});

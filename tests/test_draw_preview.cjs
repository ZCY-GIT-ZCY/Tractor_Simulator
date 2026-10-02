const {test}=require('node:test');
const assert=require('node:assert/strict');
const {cardFor}=require('../web/draw-preview.js');

test('own current draw, including id zero, is shown',()=>{
 assert.equal(cardFor({phase:'dealing',current_player:0,drawn_card:0,hand:[0,3]},0),0);
});
test('next seat drawing clears the previous player preview even in teaching mode',()=>{
 const state={phase:'dealing',current_player:1,drawn_card:42,hand:[0],hands:[[0],[42],[],[]]};
 assert.equal(cardFor(state,0),null);assert.equal(cardFor(state,1),42);
});
test('nothing from final bidding, burying or play remains in the draw slot',()=>{
 for(const phase of ['final_bidding','burying','countering','playing','round_end']){
  assert.equal(cardFor({phase,current_player:0,drawn_card:0,hand:[0]},0),null);
 }
});
test('legacy hand differences identify a new card without guessing from sorted position',()=>{
 const state={phase:'dealing',current_player:0,hand:[0,3,55]};
 assert.equal(cardFor(state,0,new Set([3,55])),0);
 assert.equal(cardFor(state,0,new Set()),null);
 assert.equal(cardFor({...state,hand:[55]},0,new Set()),55);
});
test('a redacted or unowned card is not recovered from other hands',()=>{
 const state={phase:'dealing',current_player:0,drawn_card:null,hand:[0,3]};
 assert.equal(cardFor(state,0,new Set([3])),null);
 assert.equal(cardFor({...state,drawn_card:99},0),null);
});

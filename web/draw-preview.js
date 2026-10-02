(function(root,factory){
 'use strict';
 const api=factory();
 if(typeof module==='object'&&module.exports)module.exports=api;
 else root.TractorDrawPreview=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(){
 'use strict';
 function cardFor(state,player,previousHand){
  if(!state||state.phase!=='dealing'||state.current_player!==player)return null;
  const hand=state.hands?.[player]||state.hand||[];
  if(Object.prototype.hasOwnProperty.call(state,'drawn_card'))return hand.includes(state.drawn_card)?state.drawn_card:null;
  // A page refreshed against an older live server can still identify new draws.
  if(hand.length===1)return hand[0];
  if(previousHand&&previousHand.size===hand.length-1){
   const added=hand.filter(c=>!previousHand.has(c));
   if(added.length===1)return added[0];
  }
  return null;
 }
 return Object.freeze({cardFor});
});

/* Presentation order only: no referee decisions or hidden-card information. */
(function(root, factory){
 'use strict';
 const api=factory();
 if(typeof module==='object'&&module.exports)module.exports=api;
 else root.TractorHandOrder=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(){
 'use strict';
 const suits=['S','H','D','C'], preference=['S','H','C','D'];
 const face=c=>c%54, rank=c=>face(c)<52?face(c)%13+2:face(c)-37;
 const suit=c=>face(c)<52?suits[Math.floor(face(c)/13)]:null;
 const red=s=>s==='H'||s==='D';
 const constant=(c,level)=>rank(c)===level||rank(c)>14;
 function permutations(values){
  if(!values.length)return [[]];
  return values.flatMap((v,i)=>permutations(values.filter((_,j)=>j!==i)).map(tail=>[v,...tail]));
 }
 function sort(cards,level,trump){
  const ordinary=cards.filter(c=>!constant(c,level));
  const present=preference.filter(s=>s!==trump&&ordinary.some(c=>suit(c)===s));
  const hasSuitTrump=trump&&ordinary.some(c=>suit(c)===trump);
  let order=present, best=Infinity;
  for(const candidate of permutations(present)){
   const chain=hasSuitTrump?[...candidate,trump]:candidate;
   const clashes=chain.reduce((n,s,i)=>n+(i>0&&red(s)===red(chain[i-1])?1:0),0);
   if(clashes<best){best=clashes;order=candidate;}
  }
  const group=new Map(order.map((s,i)=>[s,i]));
  const key=c=>{
   const s=suit(c),r=rank(c);
   if(constant(c,level)){
    const strength=r===level?(trump&&s===trump?1:0):r===15?2:3;
    return [2,strength,preference.indexOf(s),face(c),c];
   }
   return [s===trump?1:0,s===trump?0:group.get(s),r,face(c),c];
  };
  return [...cards].sort((a,b)=>{
   const ka=key(a),kb=key(b);
   for(let i=0;i<ka.length;i++)if(ka[i]!==kb[i])return ka[i]-kb[i];
   return 0;
  });
 }
 return Object.freeze({sort});
});

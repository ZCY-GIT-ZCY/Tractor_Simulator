(function(root,factory){
 'use strict';
 const api=factory();
 if(typeof module==='object'&&module.exports)module.exports=api;
 else root.TractorHandLayout=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(){
 'use strict';
 // Keep both ends inside a fixed hand area. A second overlapping row gives
 // every card a readable/tappable index instead of off-screen flex overflow.
 function fit(width,count,compact=false){
  const inset=Math.min(4,Math.max(0,width/4)),available=Math.max(0,width-2*inset);
  const cardWidth=Math.min(compact?54:61,available),maxStep=cardWidth*.62;
  const oneStep=count>1?(available-cardWidth)/(count-1):maxStep;
  const rows=count>1&&oneStep<18?2:1,columns=Math.ceil(count/rows);
  const positions=[];
  for(let row=0,index=0;row<rows;row++){
   const n=Math.min(columns,count-index);
   const step=n>1?Math.min(maxStep,(available-cardWidth)/(n-1)):0;
   const left=inset+(available-cardWidth-Math.max(0,n-1)*step)/2;
   for(let j=0;j<n;j++,index++)positions.push({x:left+j*step,y:rows===2&&row===0?51:0,
    indexSize:Math.max(6,Math.min(14,((n>1?step:cardWidth)-4)/1.25)),exposed:n>1?step:cardWidth});
  }
  return {cardWidth,rows,positions};
 }
 return Object.freeze({fit});
});

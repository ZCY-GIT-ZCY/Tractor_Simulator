'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {fit}=require('../web/hand-layout.js');
test('all 25/33 cards fit the measured hand width at desktop and phone breakpoints',()=>{
 for(const width of [240,300,350,550,880,1500])for(const count of [0,1,4,17,25,33]){
  const plan=fit(width,count,width<550);
  assert.equal(plan.positions.length,count);
  for(const pos of plan.positions){
   assert.ok(pos.x>=0);assert.ok(pos.x+plan.cardWidth<=width+1e-8);
   assert.ok(pos.y+plan.cardHeight+8+17<=plan.areaHeight); // Selected lift stays inside the measured height.
  }
 }
});
test('dense hands use two rows and preserve the complete rightmost card',()=>{
 for(const count of [25,33]){
  const plan=fit(350,count,true);assert.equal(plan.rows,2);
  assert.ok(plan.positions.some(p=>p.y>0));
  assert.ok(plan.positions.at(-1).x+plan.cardWidth<=350);
  assert.ok(plan.positions.every(p=>p.exposed>=17));
 }
});

'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const Clock=require('../web/replay-clock.js');
function timerClock(count=4){
 let sequence=0;const pending=new Map(),changes=[];
 const clock=new Clock(count,c=>changes.push({index:c.index,running:c.running,speed:c.speed}),{
  setTimeout(fn,ms){const id=++sequence;pending.set(id,{fn,ms});return id;},
  clearTimeout(id){pending.delete(id);}
 });
 return {clock,pending,changes,tick(){const [id,timer]=pending.entries().next().value;pending.delete(id);timer.fn();}};
}
test('manual next advances exactly one step and never starts a timer',()=>{
 const {clock,pending}=timerClock();clock.step();assert.equal(clock.index,1);assert.equal(pending.size,0);clock.step();assert.equal(clock.index,2);
});
test('default autoplay is one step per second and stops at the last step',()=>{
 const t=timerClock();t.clock.play();assert.equal(t.pending.values().next().value.ms,1000);
 t.tick();assert.equal(t.clock.index,1);t.tick();t.tick();assert.equal(t.clock.index,3);assert.equal(t.clock.running,false);assert.equal(t.pending.size,0);
});
test('continuous speed range changes the next interval without skipping steps',()=>{
 const t=timerClock();t.clock.play();t.clock.rate(.2);assert.equal(t.pending.values().next().value.ms,5000);
 t.clock.rate(1.37);assert.equal(t.pending.values().next().value.ms,1000/1.37);assert.equal(t.clock.index,0);
 t.clock.rate(5);assert.equal(t.pending.values().next().value.ms,200);assert.equal(t.pending.size,1);t.tick();assert.equal(t.clock.index,1);
});
test('manual next pauses running playback and stale callbacks do nothing',()=>{
 const t=timerClock();t.clock.play();const stale=t.pending.values().next().value.fn;t.clock.step();stale();assert.equal(t.clock.index,1);assert.equal(t.clock.running,false);assert.equal(t.pending.size,0);
});
test('seek and previous clamp to valid boundaries and pause playback',()=>{
 const t=timerClock();t.clock.seek(99);assert.equal(t.clock.index,3);t.clock.step(-1);assert.equal(t.clock.index,2);t.clock.seek(-2);assert.equal(t.clock.index,0);
 t.clock.play();t.clock.seek(2);assert.equal(t.pending.size,0);assert.equal(t.clock.running,false);
});
test('replay from the end starts again at zero; disposal cancels all timers',()=>{
 const t=timerClock();t.clock.seek(3);t.clock.play();assert.equal(t.clock.index,0);assert.equal(t.clock.running,true);t.clock.dispose();assert.equal(t.pending.size,0);
});
test('nonfinite speed is ignored; legal endpoints stay enforced',()=>{
 const t=timerClock();t.clock.rate(NaN);assert.equal(t.clock.speed,1);t.clock.rate(9);assert.equal(t.clock.speed,5);t.clock.rate(-1);assert.equal(t.clock.speed,.2);
});
test('native browser timers are never invoked with the clock as their receiver',()=>{
 const originalSet=global.setTimeout,originalClear=global.clearTimeout;
 const clock=new Clock(3,()=>{});
 global.setTimeout=function(){assert.notEqual(this,clock);return 42;};
 global.clearTimeout=function(){assert.notEqual(this,clock);};
 try{clock.play();clock.pause();}finally{global.setTimeout=originalSet;global.clearTimeout=originalClear;}
});

(function(root){
 'use strict';
 class ReplayClock {
  constructor(count,changed,timers={}){
   this.count=Math.max(1,count);this.index=0;this.speed=1;this.running=false;
   this.changed=changed;this.set=timers.setTimeout||((fn,ms)=>setTimeout(fn,ms));this.clear=timers.clearTimeout||(id=>clearTimeout(id));
   this.timer=null;this.generation=0;
  }
  cancel(){this.generation++;if(this.timer!==null)this.clear(this.timer);this.timer=null;}
  schedule(){
   this.cancel();if(!this.running)return;
   const generation=this.generation;
   this.timer=this.set(()=>{
    if(generation!==this.generation||!this.running)return;
    this.index=Math.min(this.count-1,this.index+1);
    if(this.index===this.count-1)this.running=false;
    this.changed(this);this.schedule();
   },1000/this.speed);
  }
  play(){if(this.count<=1)return;if(this.index===this.count-1)this.index=0;this.running=true;this.changed(this);this.schedule();}
  pause(){this.running=false;this.cancel();this.changed(this);}
  seek(index){this.running=false;this.cancel();this.index=Math.max(0,Math.min(this.count-1,Math.round(Number(index)||0)));this.changed(this);}
  step(delta=1){this.seek(this.index+delta);}
  rate(value){const speed=Number(value);if(!Number.isFinite(speed))return;this.speed=Math.max(.2,Math.min(5,speed));this.changed(this);this.schedule();}
  dispose(){this.running=false;this.cancel();}
 }
 root.TractorReplayClock=ReplayClock;
 if(typeof module!=='undefined'&&module.exports)module.exports=ReplayClock;
})(globalThis);

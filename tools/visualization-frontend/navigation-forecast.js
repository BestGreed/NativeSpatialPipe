// Visual-only dead reckoning. Predicted values never feed the registration solver.
const clamp=(x,a,b)=>Math.max(a,Math.min(b,x));
const blend=(a,b,t)=>a+(b-a)*t;
const finiteVector=v=>Array.isArray(v)&&v.length===3&&v.every(Number.isFinite);
const poseLimit=Math.PI/6;
export class NavigationForecast{
  constructor({horizonMs=550,correctionMs=260}={}){this.horizonMs=horizonMs;this.correctionMs=correctionMs;this.reset();}
  reset(){this.pose=null;this.flow=null;this.poseOutput=null;this.flowOutput=[0,0,0];this.poseCorrection=false;this.flowCorrection=false;this.lastStep=null;this.poseObserved=false;this.flowObserved=false;}
  observe(nav,timeMs){
    if(!Number.isFinite(timeMs))return;
    if(nav.result.stationaryLock?.active)this.reset();
    const r=nav.result,poseValid=r.valid&&r.confidence>=(r.coordinateMode==='source'?.5:.7);
    this.poseObserved=poseValid;
    if(poseValid&&Number.isFinite(nav.yaw)&&Number.isFinite(nav.pitch)){
      const value=[nav.yaw,nav.pitch],previous=this.pose,dt=previous?timeMs-previous.time:Infinity;
      const rate=dt>=40&&dt<=250?value.map((x,i)=>clamp((x-previous.value[i])/(dt/1000),i?-Math.PI: -Math.PI*2,i?Math.PI:Math.PI*2)):[0,0];
      const sparse=r.navigationCandidate?.signals?.includes('sparse-relative-pose')===true||r.confidence<.7||r.degenerate===true;
      if(this.poseOutput&&(sparse||previous?.sparse||!previous||dt>250||!this.poseObservedLast))this.poseCorrection=true;
      if(!this.poseOutput)this.poseOutput=[...value];
      const filteredRate=sparse?rate.map((v,i)=>clamp(v*.4+(previous?.rate[i]||0)*.6,-.8,.8)):rate;
      this.pose={value,rate:filteredRate,time:timeMs,confidence:r.confidence,sparse};
    }
    this.poseObservedLast=poseValid;
    const flowValid=(r.cooperativeFlow?.valid||r.valid&&r.translation)&&finiteVector(r.visualFlow);
    this.flowObserved=!!flowValid;
    if(flowValid){
      if(this.flow&&(!this.flowObservedLast||timeMs-this.flow.time>250))this.flowCorrection=true;
      if(!this.flow)this.flowOutput=[...r.visualFlow];
      this.flow={value:[...r.visualFlow],time:timeMs};
    }
    this.flowObservedLast=!!flowValid;
  }
  step(nav,nowMs,{allow=true}={}){
    const elapsed=this.lastStep===null?0:clamp(nowMs-this.lastStep,0,100);
    this.lastStep=nowMs;
    const r=nav.result,poseAge=this.pose?Math.max(0,nowMs-this.pose.time):Infinity,flowAge=this.flow?Math.max(0,nowMs-this.flow.time):Infinity;
    let poseState='none',flowState='none',yaw=nav.yaw,pitch=nav.pitch,visualFlow=null,visualAngularVelocity=[0,0,0];
    const exitWeight=age=>clamp((this.horizonMs-age)/150,0,1);
    if(allow&&this.pose&&poseAge<=this.horizonMs){
      if(this.poseObserved&&poseAge<=150){
        if(this.poseCorrection){const gain=1-Math.exp(-elapsed/this.correctionMs);this.poseOutput=this.poseOutput.map((x,i)=>blend(x,this.pose.value[i],gain));
          if(Math.abs(this.poseOutput[0]-this.pose.value[0])<.005&&Math.abs(this.poseOutput[1]-this.pose.value[1])<.005)this.poseCorrection=false;
        }else this.poseOutput=[...this.pose.value];
        poseState=this.poseCorrection?'reconciling':'observed';
      }else{
        const extrapolate=this.horizonMs*(1-Math.exp(-poseAge/this.horizonMs))/1000;
        const target=this.pose.value.map((x,i)=>x+clamp(this.pose.rate[i]*extrapolate,i?-poseLimit/2:-poseLimit,i?poseLimit/2:poseLimit));
        const gain=1-Math.exp(-elapsed/this.correctionMs);
        this.poseOutput=this.poseOutput.map((x,i)=>blend(x,target[i],gain));
        const weight=Math.exp(-poseAge/this.horizonMs)*exitWeight(poseAge);
        visualAngularVelocity=[this.pose.rate[1]*weight,this.pose.rate[0]*weight,0];
        poseState='predicted';
      }
      [yaw,pitch]=this.poseOutput;
    }
    if(allow&&this.flow&&flowAge<=this.horizonMs){
      if(this.flowObserved&&flowAge<=150){
        if(this.flowCorrection){const gain=1-Math.exp(-elapsed/this.correctionMs);this.flowOutput=this.flowOutput.map((x,i)=>blend(x,this.flow.value[i],gain));
          if(Math.hypot(...this.flowOutput.map((x,i)=>x-this.flow.value[i]))<.02)this.flowCorrection=false;
        }else this.flowOutput=[...this.flow.value];
        flowState=this.flowCorrection?'reconciling':'observed';
      }else{this.flowOutput=this.flow.value.map(x=>x*Math.exp(-flowAge/320)*exitWeight(flowAge));flowState='predicted';}
      visualFlow=[...this.flowOutput];
    }
    const prediction={pose:poseState,flow:flowState,active:poseState==='predicted'||flowState==='predicted',exitAfterMs:this.horizonMs,fadeOutMs:150,poseAgeMs:Number.isFinite(poseAge)?poseAge:null,flowAgeMs:Number.isFinite(flowAge)?flowAge:null};
    return {yaw,pitch,result:{...r,visualFlow,translation:visualFlow,visualAngularVelocity,prediction}};
  }
}

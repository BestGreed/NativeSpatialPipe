// A small visual motion cue, not a measured listener 6DoF pose.
const clamp=(v,min,max)=>Math.max(min,Math.min(max,v));
const rad=Math.PI/180;

export class ToiletFollow {
  constructor(){this.reset();}
  reset(){this.yaw=0;this.pitch=0;this.roll=0;}
  step(travel,turnRate,dt,{enabled=true}={}){
    const gain=1-Math.exp(-clamp(dt,0,.1)/.23);
    const axis=i=>enabled&&Number.isFinite(travel?.[i])?clamp(travel[i],-1,1):0;
    const turn=enabled&&Number.isFinite(turnRate)?clamp(turnRate,-1.5,1.5):0;
    // Right travel leans right; forward/back and height affect the fore-aft
    // lean. All targets remain within a few degrees.
    const targetYaw=clamp(turn*2.2*rad,-3*rad,3*rad);
    const targetPitch=clamp((-axis(2)*2.5+axis(1)*1.3)*rad,-3.5*rad,3.5*rad);
    const targetRoll=clamp(-axis(0)*3.5*rad,-4*rad,4*rad);
    this.yaw+=(targetYaw-this.yaw)*gain;
    this.pitch+=(targetPitch-this.pitch)*gain;
    this.roll+=(targetRoll-this.roll)*gain;
    return this;
  }
}

export function toiletBoundsCenter(lines){
  const low=[Infinity,Infinity,Infinity],high=[-Infinity,-Infinity,-Infinity];
  for(const edge of lines)for(const point of edge)for(let i=0;i<3;i++){
    low[i]=Math.min(low[i],point[i]);high[i]=Math.max(high[i],point[i]);
  }
  return low.map((v,i)=>(v+high[i])/2);
}

export function toiletTransform(center,follow,height=0){
  const cr=Math.cos(follow.roll),sr=Math.sin(follow.roll);
  const cp=Math.cos(follow.pitch),sp=Math.sin(follow.pitch);
  const cy=Math.cos(follow.yaw),sy=Math.sin(follow.yaw);
  return point=>{
    const x=point[0]-center[0],y=point[1]-center[1],z=point[2]-center[2];
    const xr=x*cr-y*sr,yr=x*sr+y*cr;
    const yp=yr*cp-z*sp,zp=yr*sp+z*cp;
    return [center[0]+xr*cy+zp*sy,center[1]+yp+height,center[2]-xr*sy+zp*cy];
  };
}

const clamp=(x,a=0,b=1)=>Math.max(a,Math.min(b,x));

// Semantic categories must come from a separately validated source, never from RMS alone.
export function isVoiceCategory(category){
  return category==='voice'||category==='speech'||category==='human-voice';
}

export function isVoiceVisualCategory(category){
  return isVoiceCategory(category)||category==='speech-candidate';
}

export function visualLayer(object){
  return object.kind==='dynamic'&&isVoiceVisualCategory(object.sourceCategory)?'voice':'ordinary';
}

export function voiceArcVisible(object,energy){
  return visualLayer(object)==='voice'&&Number.isFinite(energy)&&energy>.035;
}

export function voiceArcRadius(rmsDbfs){
  return .18+.26*Math.sqrt(clamp(((rmsDbfs??-60)+60)/54));
}

export function voiceArcSegments(center,radius,phase=0,normal=[0,0,1]){
  const length=Math.hypot(...center);
  const normalLength=Math.hypot(...normal);
  if(length<1e-6||!Number.isFinite(radius)||radius<=0||normalLength<1e-6)return [];
  const forward=normal.map(x=>x/normalLength),axis=Math.abs(forward[1])<.85?[0,1,0]:[1,0,0];
  const raw=[forward[1]*axis[2]-forward[2]*axis[1],forward[2]*axis[0]-forward[0]*axis[2],forward[0]*axis[1]-forward[1]*axis[0]];
  const norm=Math.hypot(...raw),u=raw.map(x=>x/norm);
  const v=[forward[1]*u[2]-forward[2]*u[1],forward[2]*u[0]-forward[0]*u[2],forward[0]*u[1]-forward[1]*u[0]];
  const point=(theta,phi)=>{
    const wave=radius*(1+.035*Math.sin(phi*3+phase+theta*6));
    return center.map((x,i)=>x+wave*(forward[i]*Math.cos(theta)+(u[i]*Math.cos(phi)+v[i]*Math.sin(phi))*Math.sin(theta)));
  };
  const segments=[];
  for(let ring=0;ring<4;ring++){
    const theta=.34+ring*.31;
    for(let i=0;i<24;i++)segments.push({a:point(theta,i*Math.PI/12),b:point(theta,(i+1)*Math.PI/12),opacity:ring===3?.3:.48});
  }
  for(let meridian=0;meridian<8;meridian++){
    const phi=meridian*Math.PI/4;
    for(let i=0;i<5;i++)segments.push({a:point(.31+i*.26,phi),b:point(.31+(i+1)*.26,phi),opacity:.38});
  }
  return segments;
}

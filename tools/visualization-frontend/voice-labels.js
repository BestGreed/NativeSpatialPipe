// Offline VAD is an activity hypothesis, not a verified semantic classification.
export function createVoiceOverlay(payload,manifest){
  if(payload?.version!==1||payload.label!=='speech-candidate'||
      payload.frameRateHz!==manifest.frameRateHz||payload.frameCount!==manifest.frameCount||
      !Number.isInteger(payload.analyzedTracks)||payload.analyzedTracks<1||payload.analyzedTracks>128||
      !payload.intervalsBySlot||typeof payload.intervalsBySlot!=='object')
    throw Error('人声候选标签与当前回放不匹配');
  const bySlot=new Map();
  for(const [key,spans] of Object.entries(payload.intervalsBySlot)){
    const slot=Number(key);
    if(!Number.isInteger(slot)||slot<12||slot>139||!Array.isArray(spans)||
        !spans.every(pair=>Array.isArray(pair)&&pair.length===2&&
          Number.isInteger(pair[0])&&Number.isInteger(pair[1])&&
          pair[0]>=0&&pair[0]<pair[1]&&pair[1]<=manifest.frameCount))
      throw Error('人声候选区间无效');
    bySlot.set(slot,spans);
  }
  return frame=>{
    const index=frame.telemetrySeq;
    const tap=frame.taps?.['decoded-input'];
    if(!tap?.objects)return frame;
    for(const object of tap.objects){
      const spans=bySlot.get(object.objectKey?.slot);
      if(object.kind==='dynamic'&&spans?.some(([start,end])=>index>=start&&index<end))
        object.sourceCategory='speech-candidate';
    }
    return frame;
  };
}

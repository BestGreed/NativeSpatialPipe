export function snapshot(i){
  const objects=Array.from({length:24},(_,slot)=>{
    const a=slot*2.399963,y=1-2*(slot+.5)/24,r=Math.sqrt(1-y*y);
    return {kind:'dynamic',objectKey:{sessionId:'test',streamEpoch:0,slot,generation:1},spatial:{positionValid:true,position:[Math.sin(a)*r*3,y*3,Math.cos(a)*r*3]},meter:{meanSquare:.01}};
  });
  return {routeId:'test',sessionId:'test',streamEpoch:0,meterRevision:'test',sourceTimeUs:(i+1)*100000,sourceSequenceRange:{start:i*10,endExclusive:(i+1)*10},taps:{'decoded-input':{quality:'live',snapshotComplete:true,objects}}};
}

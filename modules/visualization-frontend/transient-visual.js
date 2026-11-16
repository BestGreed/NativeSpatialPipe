// A presentation-only fallback for measured streams whose object lifecycle
// events do not describe abrupt changes within an existing object.
import {mapPosition} from './visual-space.js';

const CENTER_RADIUS=.30; // Visual-space radius about the listener, not a metre threshold.
export function selectVisibleOnsets(events,sourceNow,spaceScale){
  const candidates=[...events].filter(e=>e.kind==='onset'&&e.spatial?.positionValid&&
    e.spatial.position&&sourceNow>=e.sourceTimeUs&&sourceNow<e.expiresTimeUs)
    .sort((a,b)=>b.strength-a.strength);
  const primary=candidates.slice(0,6);
  const nearListener=e=>Math.hypot(...mapPosition(e.spatial.position,spaceScale))<=CENTER_RADIUS;
  if(primary.some(nearListener))return primary;
  const center=candidates.slice(6).find(nearListener);
  return center?[...primary,center]:primary;
}

export function meterOnset(previous, current, elapsedUs, lastOnsetUs, sourceTimeUs){
  if(!previous||!current||elapsedUs<=0||elapsedUs>250000||current.kind!=='dynamic'||
      !current.spatial?.positionValid||!current.spatial.position?.some(v=>Math.abs(v)>1e-6)||
      current.objectKey.generation!==previous.objectKey.generation||
      sourceTimeUs-lastOnsetUs<450000)return null;
  const db=current.meter?.rmsDbfs,prior=previous.meter?.rmsDbfs,peak=current.meter?.samplePeak;
  if(!Number.isFinite(db)||!Number.isFinite(prior)||!Number.isFinite(peak)||
      db< -42||peak<.05||db-Math.max(-90,prior)<10)return null;
  return {kind:'onset',objectKey:current.objectKey,sourceTimeUs,
    expiresTimeUs:sourceTimeUs+650000,spatial:current.spatial,
    strength:Math.min(1,Math.max(peak,.05)),risePerSecond:null,
    endReason:null,exactEosSample:null,visualInference:'meter-rise'};
}

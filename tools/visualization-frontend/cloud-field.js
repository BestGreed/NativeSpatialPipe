// A visual attack envelope for continuous sound, independent of the measured
// object position and the audio renderer's gain or distance model.
const clamp=(x,a=0,b=1)=>Math.max(a,Math.min(b,x));
const EARLY_MS=350;

export function updateCloudEnvelope(previous,db,intervalMs,continuous,sustainedMs,transient){
  if(!Number.isFinite(db)||db<=-42)return {earlyMs:0,earlyStrength:0,mix:0,visible:false};
  const prior=continuous?previous?.cloud:null;
  const earlyMs=prior?.earlyMs||0,added=Math.min(Math.max(intervalMs,0),EARLY_MS-earlyMs);
  const strength=clamp((db+42)/24);
  const nextEarlyMs=earlyMs+added;
  const earlyStrength=nextEarlyMs?(earlyMs*(prior?.earlyStrength||0)+added*strength)/nextEarlyMs:strength;
  const visible=sustainedMs>=EARLY_MS&&!transient;
  if(!visible)return {earlyMs:nextEarlyMs,earlyStrength,mix:0,visible:false};
  const entry=.32+.36*earlyStrength;
  const attackMs=1250-1000*earlyStrength;
  const mix=prior?.visible?prior.mix+(1-prior.mix)*(1-Math.exp(-intervalMs/attackMs)):entry;
  return {earlyMs:nextEarlyMs,earlyStrength,mix:clamp(mix),visible:true};
}

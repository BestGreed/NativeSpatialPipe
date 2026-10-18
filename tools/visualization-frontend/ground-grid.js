// A shared, gently eased phase keeps both directions meeting at each junction.
const flowPhase=time=>time*.73+.30*Math.sin(time*.32)+.11*Math.sin(time*.91+1.4);
const wave=(x,z,time)=>Math.pow(Math.max(0,Math.sin(flowPhase(time)-(x+z)*2.1)),6);

export function gridFlowOpacity(coordinate,crossingOffset,x,z,time){
  const cell=(coordinate-crossingOffset)/.5;
  const distance=Math.abs(cell-Math.round(cell))*.5;
  const junction=Math.exp(-Math.pow(distance/.085,2));
  const flow=wave(x,z,time);
  return Math.min(.53,.07+.27*junction*(.65+.35*flow)+.40*flow);
}

export function gridJunctionOpacity(x,z,time){
  return .08+.10*wave(x,z,time);
}

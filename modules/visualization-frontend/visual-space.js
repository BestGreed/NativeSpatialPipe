// Artistic depth mapping, independent of inference and audio renderer coordinates.
export function referenceScale(objects){
  const radii=objects.filter(o=>o.kind==='dynamic'&&o.spatial.positionValid).map(o=>Math.hypot(...o.spatial.position)).filter(r=>r>1e-6).sort((a,b)=>a-b);
  return radii.length?radii[Math.floor(radii.length*.5)]:null;
}
export function mapPosition(p,scale){
  if(!p||!p.every(Number.isFinite))return null;
  const r=Math.hypot(...p);if(r<1e-6)return [0,0,0];
  // Monotonic radial compression: different source radii never become one shell.
  const radius=1.15*Math.log1p(r/Math.max(scale||1,1e-6));
  return p.map(x=>x/r*radius);
}
// Visual priority only: the source coordinate has no calibrated metre scale.
export function cloudDepthStyle(mappedRadius){
  const outer=Math.max(0,Math.min(1,(mappedRadius-.12)/.6));
  return {opacity:.18+.82*outer,size:.55+.45*outer,spread:.45+.55*outer};
}
export function burstOffset(index,age,seed=0){
  const y=1-2*(index+.5)/18,r=Math.sqrt(1-y*y),a=index*2.39996+seed;
  const distance=.36*(1-Math.exp(-4*Math.max(0,age)));
  return [Math.cos(a)*r*distance,y*distance,Math.sin(a)*r*distance];
}

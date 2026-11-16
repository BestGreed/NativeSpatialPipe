const clamp=(x,a,b)=>Math.max(a,Math.min(b,x));

export function projectViewPoint(p,{yaw,tilt,zoom},aspect){
  const [x,y,z]=p,cy=Math.cos(yaw),sy=Math.sin(yaw),cx=Math.cos(tilt),sx=Math.sin(tilt);
  // Camera sits at (-sin(yaw)*cos(tilt), sin(tilt), cos(yaw)*cos(tilt)).
  // Screen up and depth must describe that SAME camera: depth increases away.
  const xx=x*cy+z*sy,zz=-x*sy+z*cy,yy=y*cx-zz*sx,depth=-y*sx-zz*cx;
  const perspective=3.9/Math.max(.5,3.9+depth),scale=zoom*.69;
  return [xx*scale*perspective/Math.max(.1,aspect),yy*scale*perspective,depth];
}

export function fitViewZoom(points,yaw,tilt,aspect){
  if(!points.length)return 1;
  let maxX=0,maxY=0;
  for(const p of points){
    if(!p?.every(Number.isFinite))continue;
    const q=projectViewPoint(p,{yaw,tilt,zoom:1},aspect);
    maxX=Math.max(maxX,Math.abs(q[0]));maxY=Math.max(maxY,Math.abs(q[1]));
  }
  return clamp(Math.min(.86/Math.max(maxX,.01),.76/Math.max(maxY,.01)),.05,1.8);
}

export function namedView(name,oblique,points,aspect){
  if(name==='oblique')return {...oblique};
  // Front: camera behind the model (+Z), looking forward (-Z).
  // Top: camera above (+Y), looking down. Left: camera at -X, looking right.
  const angles={front:[0,0],top:[0,Math.PI/2],left:[Math.PI/2,0]};
  if(!angles[name])throw Error('unknown view');
  const [yaw,tilt]=angles[name];
  return {yaw,tilt,zoom:fitViewZoom(points,yaw,tilt,aspect),toiletHeight:oblique.toiletHeight};
}

export function dragView(view,dx,dy){
  return {yaw:view.yaw-dx*.006,tilt:clamp(view.tilt-dy*.006,-Math.PI/2,Math.PI/2)};
}

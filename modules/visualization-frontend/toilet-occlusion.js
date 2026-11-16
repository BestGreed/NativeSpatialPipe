// CPU-side hidden-line hint for the small listener prop. The shared WebGL line
// shader has no mesh depth buffer, so projected triangles mark covered edges.
export function prepareToiletOccluders(faces,pose,project){
  const projected=[];
  for(const face of faces){
    const [a,b,c]=face.map(p=>project(pose(p)));
    const den=(b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1]);
    if(Math.abs(den)<1e-8)continue;
    projected.push({a,b,c,den,minX:Math.min(a[0],b[0],c[0]),maxX:Math.max(a[0],b[0],c[0]),
      minY:Math.min(a[1],b[1],c[1]),maxY:Math.max(a[1],b[1],c[1])});
  }
  return projected;
}

export function coveredToiletLine(a,b,occluders,project){
  const midpoint=a.map((value,i)=>(value+b[i])*.5),q=project(midpoint);
  for(const face of occluders){
    if(q[0]<face.minX||q[0]>face.maxX||q[1]<face.minY||q[1]>face.maxY)continue;
    const u=((face.b[1]-face.c[1])*(q[0]-face.c[0])+(face.c[0]-face.b[0])*(q[1]-face.c[1]))/face.den;
    const v=((face.c[1]-face.a[1])*(q[0]-face.c[0])+(face.a[0]-face.c[0])*(q[1]-face.c[1]))/face.den;
    const w=1-u-v;
    if(u<0||v<0||w<0)continue;
    const surface=u*face.a[2]+v*face.b[2]+w*face.c[2];
    if(surface<q[2]-.012)return true;
  }
  return false;
}

// Experimental receiving-direction consistency, not source power/probability.
self.onmessage=({data:d})=>{
 const {positions,cov,weights,token}=d,n=positions.length;
 const beam=(c,x,y,z)=>Math.max(0,.25*(c[0]+2*(x*c[8]+y*c[16]+z*c[24])+x*x*c[10]+y*y*c[20]+z*z*c[30]+2*(x*y*c[12]+x*z*c[14]+y*z*c[22])));
 const maxima=new Float64Array(n);
 for(let i=0;i<n;i++)for(let j=0;j<384;j++){const z=1-2*(j+.5)/384,a=j*2.399963229728653,r=Math.sqrt(1-z*z);maxima[i]=Math.max(maxima[i],beam(cov[i],r*Math.cos(a),r*Math.sin(a),z));}
 const rows=[];let maximum=-Infinity,minimum=Infinity;
 for(let iz=0;iz<22;iz++)for(let iy=0;iy<21;iy++)for(let ix=0;ix<27;ix++){
  const p=[-.4+ix*.03,-.15+iy*.03,-.25+iz*.03];let sum=0,w=0;
  for(let i=0;i<n;i++){const dx=p[0]-positions[i][0],dy=p[1]-positions[i][1],dz=p[2]-positions[i][2],r=Math.hypot(dx,dy,dz);if(r<.03||r>1.5)continue;const v=Math.min(1,beam(cov[i],dx/r,dy/r,dz/r)/Math.max(maxima[i],1e-30));sum+=weights[i]*Math.log(.1+.9*v);w+=weights[i];}
  const score=w?Math.exp(sum/w):0;rows.push([...p,score]);maximum=Math.max(maximum,score);minimum=Math.min(minimum,score);
 }
 for(const row of rows)row[3]/=Math.max(maximum,1e-30);
 self.postMessage({token,rows,relativeRange:(maximum-minimum)/Math.max(maximum,1e-30),points:n});
};

import {apply,rotation} from './navigation.js';
export const scenarios={turn:'小角度转向',large:'180° 转向 / 重定位',forward:'前进 · 可观测模拟',back:'后退 · 可观测模拟',left:'左移 · 可观测模拟',right:'右移 · 可观测模拟',diagonal:'斜向 · 可观测模拟',mixed:'共同转向 + 独立声源',still:'静止参考',sparse:'对象不足',reuse:'身份复用',gap:'参考中断'};
export const metricScenario=name=>['forward','back','left','right','diagonal'].includes(name);
export function scenarioObjects(name,time){
  const angle=name==='large'?-Math.min(Math.PI,time*Math.PI/12):['turn','mixed'].includes(name)?-.35*Math.sin(time*.55):0;
  const pitch=name==='turn'?.12*Math.sin(time*.4):0;
  const translation={forward:[0,0,time*.22],back:[0,0,-time*.22],left:[time*.22,0,0],right:[-time*.22,0,0],diagonal:[-time*.16,0,time*.16]}[name]||[0,0,0];
  return Array.from({length:name==='sparse'?3:24},(_,i)=>{
    const y=1-2*(i+.5)/24,r=Math.sqrt(1-y*y),a=i*2.39996,d=1.4+(i%6)*1.1;
    let p=apply(rotation(angle,pitch),[Math.sin(a)*r*d,y*d,Math.cos(a)*r*d]);
    p=p.map((x,j)=>x+translation[j]);
    if(name==='mixed'&&i<5)p=p.map((x,j)=>x+Math.sin(time*1.8+i+j)*1.2);
    return {slot:i,generation:name==='reuse'?1+Math.floor(time*10):1,p,peak:i<5?.15:.05};
  });
}

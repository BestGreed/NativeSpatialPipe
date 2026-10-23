import {outputLayouts} from './layout-presets.js';
const outputAliases={L:'FL',R:'FR',C:'C',LFE:'LFE',Ls:'SL',Rs:'SR',Lrs:'BL',Rrs:'BR',Lw:'FWL',Rw:'FWR',Ltf:'TFL',Rtf:'TFR',Ltm:'TML',Rtm:'TMR',Ltr:'TBL',Rtr:'TBR'};
const legacyOrder=['FL','FR','C','LFE','SL','SR','BL','BR','TFL','TFR','TBL','TBR'];
const staticTypes=Object.fromEntries(legacyOrder.map((c,i)=>[c,2**(i+1)]));
const outputOrder=m=>(outputLayouts.find(p=>'ideal'+p.id.replaceAll('.','')+'-v1'===m.layoutRevision)?.channels.map(c=>outputAliases[c]))??legacyOrder;
export function assert(ok,text){if(!ok)throw Error(text);}
export function createValidator(schema){
const near=(a,b)=>Math.abs(a-b)<=1e-8*Math.max(1,Math.abs(a),Math.abs(b));
function spatial(s){
  assert(s.positionValid===(s.position!==null),'位置有效性不一致');
  if(s.position===null){assert(s.rendererPosition===null,'未知位置不能有渲染坐标');return;}
  const [x,y,z]=s.position,r=Math.hypot(x,y,z)||1,expected=[x/r,-z/r,y/r];
  assert(s.rendererPosition!==null&&expected.every((v,i)=>near(v,s.rendererPosition[i])),'渲染坐标变换不一致');
}
function checkMeter(m){
  const n=m.sampleCount,p=m.samplePeak,e=m.sumSquares;
  if(!n){assert(e===0&&m.meanSquare===null&&p===null&&m.rmsDbfs===null&&m.peakDbfs===null&&!m.silent&&!m.overFullScale,'无数据不能标为静音或有效测量');return;}
  assert(p!==null&&m.meanSquare!==null&&near(m.meanSquare,e/n),'均方值不一致');
  assert(p*p<=e+1e-8&&e<=n*p*p+1e-8,'能量与峰值不一致');
  assert(m.silent===(e===0)&&(e===0)===(p===0),'静音标记不一致');
  assert(m.overFullScale===(p>1),'过满刻度标记不一致');
  assert(e===0?m.rmsDbfs===null:m.rmsDbfs!==null&&near(m.rmsDbfs,10*Math.log10(e/n)),'RMS 参考不一致');
  assert(p===0?m.peakDbfs===null:m.peakDbfs!==null&&near(m.peakDbfs,20*Math.log10(p)),'峰值参考不一致');
}

// This browser checks the shipped schema vocabulary, rejecting unknown fields.
function shape(value,s,path='$'){
  if(s.$ref)return shape(value,schema.$defs[s.$ref.split('/').pop()],path);
  for(const variant of ['oneOf','anyOf'])if(s[variant]){
    let count=0;for(const item of s[variant]){try{shape(value,item,path);count++;}catch{}}
    assert(variant==='oneOf'?count===1:count>0,`${path}: 数据不符合契约`);return;
  }
  if(s.enum)assert(s.enum.some(x=>x===value),`${path}: 不支持的值`);
  if(s.type){const valid={object:()=>value!==null&&typeof value==='object'&&!Array.isArray(value),array:()=>Array.isArray(value),
    integer:()=>Number.isSafeInteger(value),number:()=>typeof value==='number'&&Number.isFinite(value),
    string:()=>typeof value==='string',boolean:()=>typeof value==='boolean',null:()=>value===null};
    assert(valid[s.type](),`${path}: 类型错误`);}
  if(s.minimum!==undefined)assert(value>=s.minimum,`${path}: 小于最小值`);
  if(s.maximum!==undefined)assert(value<=s.maximum,`${path}: 超出上限`);
  for(const [limit,test] of [['minItems',(a,b)=>a>=b],['maxItems',(a,b)=>a<=b],['minLength',(a,b)=>a>=b],['maxLength',(a,b)=>a<=b]])
    if(s[limit]!==undefined)assert(test(value.length,s[limit]),`${path}: 长度错误`);
  if(s.type==='object'){
    assert(s.required.every(k=>Object.hasOwn(value,k)),`${path}: 缺少字段`);
    assert(Object.keys(value).every(k=>Object.hasOwn(s.properties,k)),`${path}: 未知字段`);
    for(const [k,v]of Object.entries(value))shape(v,s.properties[k],`${path}.${k}`);
  }
  if(s.type==='array')value.forEach((v,i)=>shape(v,s.items,`${path}[${i}]`));
}
function validate(m){
  shape(m,schema);
  if(m.messageType==='capabilities')for(const route of m.routes){
    assert(route.authority!=='native'||m.schemaVersion==='0.2.0','native authority requires 0.2.0');
    assert(!route.soundNavigation||route.decodedInput,'navigation requires decoded input');
    assert(!route.soundSpectrum||route.renderReady,'spectrum requires render output');
  }
  if(m.messageType==='snapshot'){
    assert(m.publishedTimeUs>=m.sourceTimeUs,'发布时间早于源时间');
    const span=m.audioSampleRange.endExclusive-m.audioSampleRange.start;
    assert(span>0&&m.audioSampleRange.start===m.sourceSequenceRange.start*480&&m.audioSampleRange.endExclusive===m.sourceSequenceRange.endExclusive*480,'采样范围不一致');
    for(const [name,tap]of Object.entries(m.taps)){
      const c=tap.coverage, ratio=c.expectedSamples?c.validSamples/c.expectedSamples:0;
      assert(c.expectedSamples===span&&c.validSamples<=span&&Math.abs(c.ratio-ratio)<1e-8,'覆盖率不一致');
      assert(c.validSamples%480===0&&tap.droppedBlocks*480===span-c.validSamples,'缺块计数不一致');
      assert(['expectedSamples','validSamples','ratio'].every(k=>c[k]===m.coverage[name][k])&&tap.droppedBlocks===m.droppedBlocks[name],'tap 与 envelope 不一致');
      assert(!tap.snapshotComplete||(tap.quality==='live'&&c.ratio===1),'缺口不能标记为完整快照');
      assert(tap.quality!=='live'||c.ratio===1,'连续数据必须完整覆盖');
      const ids=new Set();
      assert(name==='render-ready'?tap.gainMode==='not-applicable'&&!tap.objects.length:tap.gainMode!=='not-applicable'&&!tap.channels.length,'tap 内容不一致');
      for(const o of tap.objects){assert(o.objectKey.sessionId===m.sessionId&&o.objectKey.streamEpoch===m.streamEpoch,'对象身份不属于当前会话');assert(!ids.has(o.objectKey.slot),'对象槽位重复');ids.add(o.objectKey.slot);spatial(o.spatial);assert((o.kind==='dynamic')===(o.staticType===null),'静态类型不一致');assert(o.meter.sampleCount<=c.validSamples,'对象计量超出覆盖范围');}
      for(const item of [...tap.objects,...tap.channels]){
        checkMeter(item.meter);
      }
      for(const c of tap.channels)assert(c.meter.sampleCount===tap.coverage.validSamples&&c.validDurationUs*48000===tap.coverage.validSamples*1000000,'声道覆盖时间不一致');
    }
    assert(m.taps['render-ready'].channels.length===outputOrder(m).length&&new Set(m.taps['render-ready'].channels.map(c=>c.channelId)).size===outputOrder(m).length,'声道信息必须与所选输出布局一致');
    const ids=new Set();for(const e of m.events){assert(!ids.has(e.eventId),'重复事件');ids.add(e.eventId);assert(e.expiresTimeUs>e.sourceTimeUs&&e.sourceTimeUs<=m.sourceTimeUs,'事件时间不一致');assert(e.objectKey.sessionId===m.sessionId&&e.objectKey.streamEpoch===m.streamEpoch,'事件身份不一致');assert(e.kind==='onset'?e.endReason===null:e.endReason!==null,'事件结束信息不一致');spatial(e.spatial);}
  }else if(m.messageType==='layout'){
    const order=outputOrder(m);
    assert(m.channels.every((c,i)=>c.channelId===order[i]&&c.staticType===(staticTypes[order[i]]??null)),'布局顺序不一致');
    for(const c of m.channels)assert(c.channelId==='LFE'?c.direction===null:c.direction!==null&&near(Math.hypot(...c.direction),1),'理想方向不一致');
  }else if(m.messageType==='history'){
    const c=m.coverage,r=m.requestedRangeUs,a=m.actualRangeUs;
    assert(r.endExclusive-r.start===m.requestedWindowSeconds*1e6&&c.expectedSamples===m.requestedWindowSeconds*48000,'历史窗口不一致');
    assert(c.validSamples<=c.expectedSamples&&near(c.ratio,c.expectedSamples?c.validSamples/c.expectedSamples:0),'历史覆盖率不一致');
    assert(a!==null? a.start>=r.start&&a.endExclusive<=r.endExclusive&&a.start<a.endExclusive&&c.validSamples<=(a.endExclusive-a.start)*.048:c.validSamples===0,'历史实际范围不一致');
    const total=m.channels.reduce((s,c)=>s+c.sumSquares,0);
    assert(new Set(m.channels.map(c=>c.channelId)).size===outputOrder(m).length,'历史声道重复');
    for(const v of m.channels){assert(v.validSamples===c.validSamples&&near(v.energyIntegral,v.sumSquares/48000),'历史积分不一致');assert(near(v.contribution,total?v.sumSquares/total:0),'历史贡献不一致');assert(v.validSamples?v.meanSquare!==null&&near(v.meanSquare,v.sumSquares/v.validSamples):v.meanSquare===null&&v.samplePeak===null&&v.sumSquares===0,'历史均方值不一致');}
  }
  return m;
}

return validate;
}

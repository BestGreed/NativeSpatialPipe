// Exact module hash/version is checked by the host before attaching.
const cfg=CONFIG_PLACEHOLDER;
const mod=Process.getModuleByName('AudioSrv.dll'),saved=[];
function bytes(p,n){return Array.from(new Uint8Array(p.readByteArray(n)));}
function str(p){try{return p.readUtf16String(512).toLowerCase();}catch(_){return '';}}
function scoped(s){return s.includes(cfg.endpoint.toLowerCase());}
function hook(rva,callbacks){
 const p=mod.base.add(rva),entry={rva,before:bytes(p,32)};
 Interceptor.attach(p,callbacks);saved.push(entry);
}
hook(0x57558,{
 onEnter(a){this.target=scoped(str(a[2]))&&cfg.guids.some(g=>str(a[1]).includes(g));this.out=a[3];},
 onLeave(r){if(this.target&&r.toInt32()>=0){this.out.writeU32(1);this.out.add(4).writeU32(1);this.out.add(8).writeU64(uint64('0x24c85a5ed1c03fff'));}}
});
hook(0x57248,{
 onEnter(a){this.target=scoped(str(a[1]))&&cfg.indices.includes(a[2].toInt32());this.out=a[3];},
 onLeave(r){if(this.target&&r.toInt32()>=0){this.out.writeU8(1);this.out.add(1).writeU8(1);this.out.add(4).writeU32(0);this.out.add(8).writeU32(0);this.out.add(0x10).writeU64(uint64('0x24c85a5ed1c03fff'));}}
});
hook(0x8e304,{
 onEnter(a){this.target=scoped(str(a[1]));this.n=a[2].toUInt32();this.out=a[3];},
 onLeave(r){if(this.target&&r.toInt32()>=0&&this.n>=7)for(const i of cfg.indices){
  const p=this.out.add(i*0x130);p.add(0x18).writeU32(1);p.add(0x20).writeU64(uint64('0x24c85a5ed1c03fff'));p.add(0x28).writeU32(0);
 }}
});
Interceptor.flush();saved.forEach(s=>s.after=bytes(mod.base.add(s.rva),32));
let cleaned=false,lastLease=Date.now();
function cleanup(){
 if(!cleaned){Interceptor.detachAll();Interceptor.flush();cleaned=true;}
 return saved.map(s=>({rva:s.rva,restored:JSON.stringify(bytes(mod.base.add(s.rva),32))===JSON.stringify(s.before)}));
}
// The resident renews this independently of FSA/NSp. Host failure invalidates
// the memory tag immediately and removes hooks within 5 seconds.
setInterval(()=>{if(Date.now()-lastLease>5000)cleanup();},1000);
rpc.exports={
 renew(){lastLease=Date.now();return !cleaned&&saved.every(s=>JSON.stringify(bytes(mod.base.add(s.rva),32))===JSON.stringify(s.after));},
 cleanup,snapshot(){return {pid:Process.id,module:mod.path,entries:saved};}
};

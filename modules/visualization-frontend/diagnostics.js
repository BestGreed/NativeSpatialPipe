// Bounded connection/error history only; never stores audio or full snapshots.
export class FrontendDiagnostics {
  constructor({storage=null,clock=()=>Date.now(),limit=80}={}){
    this.storage=storage;this.clock=clock;this.limit=limit;this.key='nsp.frontendDiagnostics';this.events=[];
    try{const saved=JSON.parse(storage?.getItem(this.key)||'[]');
      if(Array.isArray(saved))this.events=saved.filter(e=>e&&typeof e.kind==='string'&&typeof e.message==='string'&&typeof e.time==='string').slice(-limit);
    }catch{}
  }
  record(kind,message,details={}){
    const now=this.clock(),last=this.events.at(-1),text=String(message).slice(0,600);
    if(last?.kind===kind&&last.message===text&&now-Date.parse(last.time)<2000)return null;
    const safe={};for(const [key,value]of Object.entries(details))
      if(value===null||typeof value==='boolean'||typeof value==='number'&&Number.isFinite(value)||typeof value==='string')safe[key]=typeof value==='string'?value.slice(0,600):value;
    const event={time:new Date(now).toISOString(),kind:String(kind).slice(0,80),message:text,details:safe};
    this.events.push(event);if(this.events.length>this.limit)this.events.splice(0,this.events.length-this.limit);
    try{this.storage?.setItem(this.key,JSON.stringify(this.events));}catch{}
    return event;
  }
}

"""Bounded in-memory diagnostic events; serialized only after capture stops."""
import gc,threading,time
class TailTrace:
    def __init__(self,capacity=2048,threshold_ms=2):
        self.capacity=capacity;self.threshold_ns=int(threshold_ms*1e6);self.events=[];self.dropped=0;self.gc_starts={}
        self.previous_gc=gc.isenabled();self.callback=self.gc_callback;gc.callbacks.append(self.callback)
    def add(self,kind,start,end,**fields):
        if len(self.events)>=self.capacity:self.dropped+=1;return
        self.events.append(dict(kind=kind,startQpcNs=start,endQpcNs=end,durationMs=(end-start)/1e6,threadId=threading.get_native_id(),**fields))
    def slow(self,kind,start,end,**fields):
        if end-start>=self.threshold_ns:self.add(kind,start,end,**fields)
    def gc_callback(self,phase,info):
        now=time.perf_counter_ns();key=threading.get_ident(),info['generation']
        if phase=='start':self.gc_starts[key]=now
        else:
            start=self.gc_starts.pop(key,now)
            self.add('pythonGC',start,now,generation=info['generation'],collected=info.get('collected'),uncollectable=info.get('uncollectable'))
    def finish(self):
        gc.callbacks.remove(self.callback)
        return dict(scope='Bounded in-process timing; GC overlaps are associations, not causal proof. No stacks or kernel scheduling trace.',thresholdMs=self.threshold_ns/1e6,capacity=self.capacity,dropped=self.dropped,events=self.events)

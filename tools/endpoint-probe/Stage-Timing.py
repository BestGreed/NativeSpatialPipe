"""Bounded 1 us histograms. Same instrumentation for all comparison profiles."""
class Timings:
    def __init__(self):self.stages={}
    def add(self,name,ns):
        if ns<0:raise ValueError('QPC clock mismatch')
        h=self.stages.get(name)
        if h is None:
            h={'bins':[0]*50001,'count':0,'sum':0,'max':0};self.stages[name]=h
        h['bins'][min(50000,ns//1000)]+=1;h['count']+=1;h['sum']+=ns;h['max']=max(h['max'],ns)
    def report(self):
        result={}
        for name,h in self.stages.items():
            def percentile(q):
                target=max(1,__import__('math').ceil(h['count']*q));n=0
                for i,v in enumerate(h['bins']):
                    n+=v
                    if n>=target:return i/1000
            result[name]={'count':h['count'],'mean':h['sum']/h['count']/1e6,'p50':percentile(.5),'p95':percentile(.95),'p99':percentile(.99),'max':h['max']/1e6,'overflow':h['bins'][-1]}
        return {'units':'ms','histogramBinUs':1,'percentileCeilingMs':50,'stages':result}

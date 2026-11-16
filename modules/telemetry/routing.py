"""Telemetry-only selection from existing render-output progress.

Never called by Hook, receiver, renderer or writer. Reads file length only;
does not read PCM, submit packets, change rendering routes or wait for audio.
"""
import time


class TelemetryRoutes:
    def __init__(self, *, interval=.1, stale_after=1., clock=time.monotonic, size_reader=None):
        self.interval=interval;self.stale_after=stale_after;self.clock=clock
        self.size_reader=size_reader;self.next_poll=0.;self.routes={};self.selected=None
        self.selections=0

    def _size(self, pipeline, row):
        if self.size_reader is not None:return self.size_reader(pipeline)
        # Separate read-only handle: neither changes the writer's file position
        # nor reads its samples. Buffered writer output may trail rendering.
        if row['file'] is None:
            row['file']=(pipeline.out/'roar-714.f32').open('rb',buffering=0)
        return row['file'].seek(0,2)

    def select(self, pipelines):
        now=self.clock()
        if now<self.next_poll:return self.selected
        self.next_poll=now+self.interval
        for index,pipeline in enumerate(pipelines):
            row=self.routes.setdefault(pipeline,dict(index=index,size=None,last=None,since=None,updates=0,file=None,error=None))
            try:
                size=self._size(pipeline,row);row['error']=None
            except OSError as exc:
                row['error']=type(exc).__name__;continue
            if row['size'] is not None and size>row['size']:
                if row['last'] is None or now-row['last']>self.stale_after:
                    row['since']=now;row['updates']=0
                row['last']=now;row['updates']+=1
            elif row['size'] is not None and size<row['size']:
                row['last']=row['since']=None;row['updates']=0
            row['size']=size
        live=[p for p in pipelines if self.routes[p]['error'] is None and self.routes[p]['last'] is not None
              and now-self.routes[p]['last']<=self.stale_after]
        current=self.selected if self.selected in live else None
        # Keep a live selection stable against alternating writes. A newer or
        # resumed route must advance twice before it preempts a healthy route.
        candidates=[p for p in live if current is None or p is current or self.routes[p]['updates']>=2]
        selected=max(candidates,key=lambda p:(self.routes[p]['since'],-self.routes[p]['index']),default=None)
        if selected is not self.selected:self.selections+=1
        self.selected=selected
        return selected

    def status(self):
        now=self.clock()
        return dict(policy='latest-active-render-output',pollSeconds=self.interval,staleAfterSeconds=self.stale_after,
            selectedPipeline=self.routes[self.selected]['index'] if self.selected is not None else None,
            selectionChanges=self.selections,routes=[dict(pipeline=r['index'],outputBytes=r['size'],
                ageSeconds=None if r['last'] is None else max(0,now-r['last']),
                progressUpdates=r['updates'],error=r['error']) for r in self.routes.values()])

    def close(self):
        for row in self.routes.values():
            if row['file'] is not None:row['file'].close();row['file']=None

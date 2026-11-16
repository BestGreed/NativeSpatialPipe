"""Object-based soundscape visualization / 基于对象的声景可视化.

All sample analysis and lifecycle/event aggregation live in the service process.
"""
from collections import OrderedDict
import math
import struct
from engine import Engine, HEADER, meter, cov, contract

ROW=struct.Struct('<IIIIQ3fIfI')
STRIDE=ROW.size+480*4


def spatial(raw):
    r=math.sqrt(sum(v*v for v in raw)) if raw is not None else 0
    s=1/r if r else 1
    return dict(position=raw,positionValid=raw is not None,
        coordinateSystem='listener-relative-x-right-y-up-z-back',provenance='raw',units='source-unverified',
        rendererPosition=[raw[0]*s,-raw[2]*s,raw[1]*s] if raw is not None else None,rendererTransform='normalize-x-negz-y')


class SoundscapeEngine(Engine):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.object_epoch=None;self.object_frames=OrderedDict();self.object_track={}
        self.object_received=0;self.object_invalid=0;self.object_last=-1;self.last_input=None
        self.object_meta={};self.in_ingest=False
    def ingest(self,raw):
        self.in_ingest=True
        try:return super().ingest(raw)
        finally:self.in_ingest=False
    def capabilities(self):
        caps=super().capabilities()
        caps['routes'][0].update(soundNavigation=True,decodedInput=True)
        return caps
    def ingest_objects(self,raw):
        if len(raw)<32:self.object_invalid+=1;return False
        magic,version,count,frames,rate,epoch,seq=HEADER.unpack_from(raw)
        if (magic,version,frames,rate)!=(b'NSPO',1,480,48000) or not 1<=count<=160 or len(raw)!=32+count*STRIDE or epoch%2!=1 or epoch>contract.MAX_INT or seq>=contract.MAX_INT//10000:
            self.object_invalid+=1;return False
        if self.object_epoch is not None and (epoch<self.object_epoch or epoch==self.object_epoch and seq<=self.object_last):
            self.object_invalid+=1;return False
        rows=[];seen=set();mode=None
        measured=self.metering.rows(raw,32+ROW.size,STRIDE,count)
        if measured is None:self.object_invalid+=1;return False
        for i in range(count):
            at=32+i*STRIDE
            slot,ident,typ,flags,gen,x,y,z,valid,gain,applied=ROW.unpack_from(raw,at)
            if slot in seen or slot>=160 or typ not in (1,*contract.BED_TYPES) or not 1<=gen<=contract.MAX_INT or valid not in (0,1) or applied not in (0,1) or not all(map(math.isfinite,(x,y,z,gain))) or mode is not None and mode!=applied:
                self.object_invalid+=1;return False
            seen.add(slot);mode=applied
            previous=self.object_meta.get(slot) if epoch==self.object_epoch else None
            if previous and (gen<previous[0] or gen==previous[0] and (ident,typ)!=previous[1:]):
                self.object_invalid+=1;return False
            rows.append(dict(slot=slot,ident=ident,typ=typ,flags=flags,gen=gen,
                position=[x,y,z] if valid else None,energy=measured[i][0],peak=measured[i][1]))
        if epoch!=self.object_epoch:
            self.object_epoch=epoch;self.object_frames.clear();self.object_track.clear();self.last_input=None
            self.object_meta.clear()
        self.object_meta={r['slot']:(r['gen'],r['ident'],r['typ']) for r in rows}
        self.object_last=seq;self.object_received+=1
        self.object_frames[seq]=dict(rows=rows,mode=mode)
        while len(self.object_frames)>32:self.object_frames.popitem(last=False)
        return True
    def flush(self,force=False):
        if self.batch is None or self.in_ingest:return
        # Give independently transported input a short bounded chance to catch up.
        if not force:
            if self.clock()<self.batch['deadline']:return
            caught_up=self.object_epoch==self.epoch and self.batch['end']-1 in self.object_frames
            if not caught_up and self.clock()<self.batch['deadline']+.02:return
        old=self.latest
        super().flush(force=True)
        if self.latest is old:return
        message=self.latest;start=message['sourceSequenceRange']['start'];end=message['sourceSequenceRange']['endExclusive']
        frames=[(seq,frame) for seq,frame in self.object_frames.items() if start<=seq<end] if self.object_epoch==self.epoch else []
        totals={};events=[];overflow=0;mode='ignored';last_rows=[]
        for seq,frame in frames:
            continuous=self.last_input is None or seq==self.last_input+1
            if not continuous:self.object_track.clear()
            current={};mode='applied' if frame['mode'] else 'ignored'
            for row in frame['rows']:
                key=(row['slot'],row['gen'])
                previous=self.object_track.get(row['slot'])
                created=previous['created'] if previous and previous['key']==key else seq
                aggregate=totals.get(key)
                if aggregate is None:
                    aggregate=totals[key]=dict(n=0,energy=0.,peak=0.)
                aggregate['n']+=480;aggregate['energy']+=row['energy'];aggregate['peak']=max(aggregate['peak'],row['peak'])
                onset=(bool(row['flags']&1) or continuous and (previous is None or previous['key']!=key)) and row['peak']>.02
                ended=bool(row['flags']&8)
                if onset or ended:
                    identity=dict(sessionId=self.session,streamEpoch=self.epoch,slot=row['slot'],generation=row['gen'])
                    position=spatial(row['position'])
                    for kind in (['onset'] if onset else [])+(['ended'] if ended else []):
                        source=self.base+(seq+1-self.origin)*10000
                        event=dict(eventId=f'{seq}:{row["slot"]}:{row["gen"]}:{kind}',objectKey=identity,kind=kind,
                            sourceTimeUs=source,expiresTimeUs=source+500000,spatial=position,strength=row['peak'],
                            risePerSecond=None,endReason='source-flag' if kind=='ended' else None,exactEosSample=None)
                        if len(events)<256:events.append(event)
                        else:overflow+=1
                if not ended:
                    current[row['slot']]=dict(key=key,created=created,row=row)
            self.object_track=current;self.last_input=seq
            last_rows=[(v['row'],v['created']) for v in current.values()]
        n=len(frames)*480;expected=(end-start)*480
        complete=n==expected
        objects=[]
        for row,created in last_rows:
            t=totals[(row['slot'],row['gen'])]
            identity=dict(sessionId=self.session,streamEpoch=self.epoch,slot=row['slot'],generation=row['gen'])
            objects.append(dict(objectKey=identity,sourceIdent=row['ident'],kind='dynamic' if row['typ']==1 else 'static',
                staticType=None if row['typ']==1 else row['typ'],spatial=spatial(row['position']),meter=meter(t['n'],t['energy'],t['peak']),
                activeDurationUs=(frames[-1][0]-created+1)*10000,applicationName=None,sourceCategory=None))
        tap=dict(quality='live' if complete else 'gap' if frames else 'disconnected',coverage=cov(n,expected),
            droppedBlocks=(expected-n)//480,snapshotComplete=complete,gainMode=mode,objects=objects,channels=[])
        message['taps']['decoded-input']=tap;message['coverage']['decoded-input']=dict(tap['coverage'])
        message['droppedBlocks']['decoded-input']=tap['droppedBlocks'];message['events']=events;message['eventOverflow']=overflow
        message['meterRevision']='linear-v2-object-and-output'
        contract.validate(message)
        for seq in list(self.object_frames):
            if seq<end:del self.object_frames[seq]
    def history(self,*args,**kwargs):
        history=super().history(*args,**kwargs);history['meterRevision']='linear-v2-object-and-output'
        return history
    def status(self):
        return dict(super().status(),objectBlocks=self.object_received,invalidObjectBlocks=self.object_invalid,
            pendingObjectFrames=len(self.object_frames))

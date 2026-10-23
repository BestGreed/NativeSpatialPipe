"""Output metering/history: run exclusively in the telemetry service process."""
from collections import OrderedDict
import copy
import importlib.util
import math
from pathlib import Path
import struct
import time
import uuid
from metering import Metering
from layout_presets import LAYOUTS

spec = importlib.util.spec_from_file_location('output_contract', Path(__file__).resolve().parents[1] / 'visualization-contract/contract.py')
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)
HEADER = struct.Struct('<4sHHIIQQ')
BLOCK_BYTES = 32 + 12 * 480 * 4
VERSION = '0.2.0'


def meter(n=0, energy=0., peak=None):
    return dict(sampleCount=n, sumSquares=energy, meanSquare=energy/n if n else None,
        samplePeak=peak, rmsDbfs=10*math.log10(energy/n) if energy else None,
        peakDbfs=20*math.log10(peak) if peak else None, silent=bool(n and energy == 0), overFullScale=bool(peak and peak > 1))


def cov(n, expected):
    return dict(expectedSamples=expected, validSamples=n, ratio=n/expected if expected else 0)


class Engine:
    def __init__(self, route='native-route', clock=time.monotonic, meter_backend=None, output_layout='7.1.4'):
        self.channels=LAYOUTS[output_layout]
        self.channel_count=len(self.channels)
        self.output_layout=output_layout
        self.layout_revision='ideal'+output_layout.replace('.','')+'-v1'
        self.layout_id=('roar-layout' if output_layout=='7.1.4' else 'nsp-layout')+output_layout.replace('.','')
        self.block_bytes=32+self.channel_count*480*4
        self.metering = Metering(meter_backend)
        self.route = route
        self.session = str(uuid.uuid4())
        self.clock = clock
        self.started = clock()
        self.epoch = None
        self.origin = self.base = self.last_source = 0
        self.next_seq = None
        self.batch = None
        self.latest = None
        self.buckets = OrderedDict()
        self.telemetry_seq = 0
        self.received = self.invalid = self.reordered = self.source_gaps = 0
        self.last_received = None
        self.last_end = 0

    def us(self):
        return max(0, int((self.clock() - self.started)*1000000))

    def layout(self):
        directions=dict(zip(contract.CHANNELS,[(-30,0),(30,0),(0,0),None,(-90,0),(90,0),(-150,0),(150,0),(-30,45),(30,45),(-150,45),(150,45)]))
        directions.update(FWL=(-60,0),FWR=(60,0),TML=(-90,45),TMR=(90,45))
        if self.output_layout.startswith('5.') : directions.update(SL=(-110,0),SR=(110,0))
        types=dict(zip(contract.CHANNELS,contract.BED_TYPES))
        rows = []
        for name in self.channels:
            typ=types.get(name);angle=directions[name]
            d = None
            if angle is not None:
                az, el = map(math.radians, angle)
                d = [math.sin(az)*math.cos(el), math.sin(el), -math.cos(az)*math.cos(el)]
            rows.append(dict(channelId=name, staticType=typ, direction=d))
        return dict(schemaVersion=VERSION, messageType='layout', dataOrigin='measured', routeId=self.route,
            layoutRevision=self.layout_revision, layoutId=self.layout_id, coordinateSystem='listener-relative-x-right-y-up-z-back', positionSource='idealized', channels=rows)

    def capabilities(self):
        return dict(schemaVersion=VERSION, messageType='capabilities', dataOrigin='measured',
            routes=[dict(routeId=self.route, authority='native', soundNavigation=False, soundSpectrum=True,
            decodedInput=False, renderReady=True, frequencyBands=False, history=True, lufs=False, truePeak=False,
            devicePlayback=False, control=False, maxObjects=160, maxChannels=self.channel_count)], publishRatesHz=[1,10], historyWindowsSeconds=[600,3600])

    def ingest(self, raw):
        if len(raw) != self.block_bytes:
            self.invalid += 1; return False
        magic, version, channels, frames, rate, epoch, seq = HEADER.unpack_from(raw)
        if (magic,version,channels,frames,rate) != (b'NSPT',1,self.channel_count,480,48000) or epoch % 2 != 1 or seq >= contract.MAX_INT//10000 or epoch > contract.MAX_INT:
            self.invalid += 1; return False
        if self.epoch is not None and (epoch < self.epoch or epoch == self.epoch and seq < self.next_seq):
            self.reordered += 1; return False
        if epoch != self.epoch:
            self.base = max(self.last_source, self.us())
            self.epoch, self.origin, self.next_seq = epoch, seq, seq
            self.batch = self.latest = None
            self.buckets.clear()
            self.telemetry_seq = 0
        start = self.next_seq
        self.source_gaps += seq - start
        self.next_seq = seq + 1
        self.last_end = seq + 1
        self.last_received = self.clock()
        self.received += 1
        measured = self.metering.rows(raw, HEADER.size, 480*4, self.channel_count)
        finite = measured is not None
        if not finite:
            self.invalid += 1
        if self.batch is None:
            self.batch = dict(start=start, end=seq+1, n=0, energy=[0.]*self.channel_count, peak=[None]*self.channel_count, deadline=self.clock()+.1)
        batch = self.batch
        batch['end'] = seq+1
        if finite:
            batch['n'] += 480
            tag = (seq-self.origin)//100
            bucket = self.buckets.setdefault(tag, dict(first=seq, end=seq+1, n=0, energy=[0.]*self.channel_count, peak=[None]*self.channel_count))
            bucket['end'] = seq+1
            bucket['n'] += 480
            for c,(energy,peak) in enumerate(measured):
                for target in (batch,bucket):
                    target['energy'][c] += energy
                    target['peak'][c] = max(target['peak'][c] or 0, peak)
        tag = (seq-self.origin)//100
        while self.buckets and next(iter(self.buckets)) < tag-3599:
            self.buckets.popitem(last=False)
        self.last_source = self.base + (seq+1-self.origin)*10000
        self.flush()
        return True

    def flush(self, force=False):
        b = self.batch
        if b is None or not force and self.clock() < b['deadline']:
            return
        expected = (b['end']-b['start'])*480
        output = dict(quality='live' if b['n']==expected else 'gap', coverage=cov(b['n'],expected),
            droppedBlocks=(expected-b['n'])//480, snapshotComplete=b['n']==expected,
            gainMode='not-applicable', objects=[], channels=[dict(channelId=name,band='all',
            meter=meter(b['n'],b['energy'][c],b['peak'][c]),validDurationUs=b['n']*1000000//48000) for c,name in enumerate(self.channels)])
        missing = dict(quality='disconnected',coverage=cov(0,expected),droppedBlocks=expected//480,
            snapshotComplete=False,gainMode='ignored',objects=[],channels=[])
        source = self.base+(b['end']-self.origin)*10000
        message = dict(schemaVersion=VERSION,messageType='snapshot',dataOrigin='measured',routeId=self.route,
            sessionId=self.session,streamEpoch=self.epoch,telemetrySeq=self.telemetry_seq,
            sourceSequenceRange=dict(start=b['start'],endExclusive=b['end']),
            audioSampleRange=dict(start=b['start']*480,endExclusive=b['end']*480), sampleIndexProvenance='derived',
            framesPerBlock=480,sampleRate=48000,layoutRevision=self.layout_revision,meterRevision='render-ready-linear-v1',
            sourceTimeUs=source,publishedTimeUs=max(source,self.us()),
            coverage={'decoded-input':copy.deepcopy(missing['coverage']),'render-ready':copy.deepcopy(output['coverage'])},
            droppedBlocks={'decoded-input':missing['droppedBlocks'],'render-ready':output['droppedBlocks']},
            eventOverflow=0,taps={'decoded-input':missing,'render-ready':output},events=[])
        contract.validate(message)
        self.latest = message
        self.telemetry_seq += 1
        self.batch = None

    def history(self, window=600, band='all'):
        if type(window) is not int or window not in (600,3600) or band != 'all':
            raise ValueError('Unsupported history query')
        end = max((self.last_end-self.origin)*10000,self.us()-self.base) if self.epoch is not None else 0
        start = max(0,end-window*1000000)
        request_end = start+window*1000000
        selected = [b for tag,b in self.buckets.items() if tag*1000000 >= start and (b['end']-self.origin)*10000 <= request_end]
        n = sum(b['n'] for b in selected)
        energy = [math.fsum(b['energy'][c] for b in selected) for c in range(self.channel_count)]
        total = math.fsum(energy)
        actual = None
        if selected:
            actual = dict(start=self.base+(min(b['first'] for b in selected)-self.origin)*10000,
                endExclusive=self.base+(max(b['end'] for b in selected)-self.origin)*10000)
        message = dict(schemaVersion=VERSION,messageType='history',dataOrigin='measured',routeId=self.route,
            sessionId=self.session,streamEpoch=self.epoch or 0,layoutRevision=self.layout_revision,meterRevision='render-ready-linear-v1',
            tap='render-ready',sampleRate=48000,band='all',requestedWindowSeconds=window,
            requestedRangeUs=dict(start=self.base+start,endExclusive=self.base+request_end),actualRangeUs=actual,
            bucketWidthUs=1000000,boundaryErrorUs=1000000,coverage=cov(n,window*48000),
            channels=[dict(channelId=name,sumSquares=energy[c],validSamples=n,
                samplePeak=max((b['peak'][c] for b in selected),default=None),meanSquare=energy[c]/n if n else None,
                energyIntegral=energy[c]/48000,contribution=energy[c]/total if total else 0) for c,name in enumerate(self.channels)])
        return contract.validate(message)

    def status(self):
        age = self.clock()-self.last_received if self.last_received is not None else None
        return dict(status='warmup' if age is None else 'stale' if age>.25 else 'live',meterBackend=self.metering.name,
            receivedBlocks=self.received,invalidBlocks=self.invalid,reorderedBlocks=self.reordered,
            unobservedBlocks=self.source_gaps,historyBuckets=len(self.buckets))

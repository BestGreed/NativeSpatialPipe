"""Deterministic synthetic fixtures; never reads or publishes live audio."""
import copy
import json
import math
from pathlib import Path

from contract import CHANNELS, BED_TYPES, ROOT, SCHEMA, validate

DEST = ROOT / 'contracts' / 'visualization' / 'v0'


def meter(samples):
    n = len(samples)
    energy = math.fsum(x*x for x in samples)
    peak = max((abs(x) for x in samples), default=None)
    return dict(sampleCount=n, sumSquares=energy, meanSquare=energy/n if n else None,
                samplePeak=peak, rmsDbfs=10*math.log10(energy/n) if energy else None,
                peakDbfs=20*math.log10(peak) if peak else None,
                silent=bool(n and energy == 0), overFullScale=bool(peak and peak > 1))


def coverage(valid, expected):
    return dict(expectedSamples=expected, validSamples=valid, ratio=valid/expected if expected else 0)


def spatial(raw):
    radius = math.sqrt(sum(x*x for x in raw)) if raw is not None else 0
    scale = 1 / radius if radius else 1
    return dict(position=raw, positionValid=raw is not None,
                coordinateSystem='listener-relative-x-right-y-up-z-back', provenance='raw',
                units='source-unverified', rendererTransform='normalize-x-negz-y',
                rendererPosition=[raw[0]*scale, -raw[2]*scale, raw[1]*scale] if raw is not None else None)


def object_item(slot=0, generation=1, raw=None):
    return dict(objectKey=dict(sessionId='fixture-session', streamEpoch=0, slot=slot, generation=generation),
                sourceIdent=42, kind='dynamic', staticType=None, spatial=spatial(raw),
                meter=meter([0.0]*2400), activeDurationUs=0, applicationName=None, sourceCategory=None)


def snapshot(quality='live', missing_blocks=0):
    count = (5-missing_blocks)*480
    c = coverage(count, 2400)
    sine = [math.sin(2*math.pi*1000*i/48000) for i in range(count)]
    taps = {}
    for name in ('decoded-input', 'render-ready'):
        taps[name] = dict(quality=quality, coverage=copy.deepcopy(c), droppedBlocks=missing_blocks,
                          snapshotComplete=quality == 'live', gainMode='ignored' if name == 'decoded-input' else 'not-applicable',
                          objects=[], channels=[])
    taps['render-ready']['channels'] = [dict(channelId=name, band='all',
        meter=meter(sine if name == 'FL' else [0.0]*count), validDurationUs=count*1000000//48000) for name in CHANNELS]
    return dict(schemaVersion='0.1.0', messageType='snapshot', dataOrigin='simulated', routeId='fixture-route',
                sessionId='fixture-session', streamEpoch=0, telemetrySeq=0,
                sourceSequenceRange=dict(start=0, endExclusive=5), audioSampleRange=dict(start=0, endExclusive=2400),
                sampleIndexProvenance='derived', framesPerBlock=480, sampleRate=48000,
                layoutRevision='ideal714-v1', meterRevision='linear-v1-gain-ignored',
                sourceTimeUs=50000, publishedTimeUs=51000,
                coverage={name: copy.deepcopy(c) for name in taps},
                droppedBlocks={name: missing_blocks for name in taps}, eventOverflow=0, taps=taps, events=[])


def layout():
    # Presentation choices, not measured speaker locations or claimed ROAR angles.
    angles = [(-30, 0), (30, 0), (0, 0), None, (-90, 0), (90, 0),
              (-150, 0), (150, 0), (-30, 45), (30, 45), (-150, 45), (150, 45)]
    rows = []
    for name, typ, angle in zip(CHANNELS, BED_TYPES, angles):
        d = None
        if angle:
            az, el = map(math.radians, angle)
            d = [math.sin(az)*math.cos(el), math.sin(el), -math.cos(az)*math.cos(el)]
        rows.append(dict(channelId=name, staticType=typ, direction=d))
    return dict(schemaVersion='0.1.0', messageType='layout', dataOrigin='simulated', routeId='fixture-route',
                layoutRevision='ideal714-v1', layoutId='roar-layout714',
                coordinateSystem='listener-relative-x-right-y-up-z-back', positionSource='idealized', channels=rows)


def history(window=600, valid_seconds=2, silent=False):
    n = valid_seconds*48000
    channels = []
    for name in CHANNELS:
        energy = n*0.5 if name == 'FL' and not silent else 0
        channels.append(dict(channelId=name, sumSquares=energy, validSamples=n,
                             samplePeak=(1 if energy else 0) if n else None,
                             meanSquare=energy/n if n else None, energyIntegral=energy/48000,
                             contribution=1 if energy else 0))
    return dict(schemaVersion='0.1.0', messageType='history', dataOrigin='simulated', routeId='fixture-route',
                sessionId='fixture-session', streamEpoch=0, layoutRevision='ideal714-v1',
                meterRevision='linear-v1-gain-ignored', tap='render-ready', sampleRate=48000, band='all',
                requestedWindowSeconds=window, requestedRangeUs=dict(start=0, endExclusive=window*1000000),
                actualRangeUs=dict(start=0, endExclusive=valid_seconds*1000000) if valid_seconds else None,
                bucketWidthUs=1000000, boundaryErrorUs=1000000,
                coverage=coverage(n, window*48000), channels=channels)


def examples():
    live = snapshot()
    axes = [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1], None, [0, 0, 0]]
    live['taps']['decoded-input']['objects'] = [object_item(i, raw=raw) for i, raw in enumerate(axes)]
    short = snapshot()
    short['events'] = [dict(eventId='short-' + kind, objectKey=object_item()['objectKey'], kind=kind,
        sourceTimeUs=time, expiresTimeUs=time+500000, spatial=spatial([0, 0, -2]), strength=1,
        risePerSecond=100 if kind == 'onset' else None,
        endReason=None if kind == 'onset' else 'source-flag', exactEosSample=None)
        for kind, time in [('onset', 10000), ('ended', 20000)]]
    caps = dict(schemaVersion='0.1.0', messageType='capabilities', dataOrigin='simulated',
                routes=[dict(routeId='fixture-route', authority='fixture', soundNavigation=True,
                             soundSpectrum=True, decodedInput=True, renderReady=True, frequencyBands=False,
                             history=True, lufs=False, truePeak=False, devicePlayback=False,
                             control=False, maxObjects=160, maxChannels=12)],
                publishRatesHz=[1, 10, 20], historyWindowsSeconds=[600, 3600])
    request = dict(schemaVersion='0.1.0', messageType='subscribe', routeId='fixture-route',
                   mode='both', publishRateHz=20, bands=['all'])
    result = {'capabilities': caps, 'layout': layout(), 'subscribe': request,
              'negotiated': dict(request, messageType='negotiated', publishRateHz=10),
              'live-six-axis': live, 'short-lifecycle': short, 'gap': snapshot('gap', 1),
              'disconnected': snapshot('disconnected', 5), 'warmup': snapshot('warmup'),
              'stale': snapshot('stale', 5), 'history-partial-600': history(),
              'history-silent-3600': history(3600, 3600, True), 'history-empty': history(valid_seconds=0)}
    silent = snapshot()
    for c in silent['taps']['render-ready']['channels']:
        c['meter'] = meter([0]*2400)
    result['silence'] = silent
    over = snapshot()
    over['taps']['render-ready']['channels'][0]['meter'] = meter([2]*2400)
    result['over-full-scale'] = over
    reuse = snapshot()
    reuse['taps']['decoded-input']['objects'] = [object_item(0, 2, [1, 0, 0])]
    result['slot-reuse'] = reuse
    gain = snapshot()
    gain['meterRevision'] = 'linear-v1-gain-applied'
    gain['taps']['decoded-input']['gainMode'] = 'applied'
    o = object_item(0, raw=[0, 0, -1])
    # Constant 0.25 for 10 ms, then gain 2 -> 0.5 for 40 ms.
    o['meter'] = meter([0.25]*480 + [0.5]*1920)
    o['activeDurationUs'] = 50000
    gain['taps']['decoded-input']['objects'] = [o]
    result['gain-step'] = gain
    return result


def export():
    DEST.mkdir(parents=True, exist_ok=True)
    (DEST/'fixtures').mkdir(exist_ok=True)
    (DEST/'schema.json').write_text(json.dumps(SCHEMA, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    for name, value in examples().items():
        validate(value)
        (DEST/'fixtures'/(name+'.json')).write_text(json.dumps(value, indent=2, allow_nan=False)+'\n', encoding='utf-8')


if __name__ == '__main__':
    export()
    print(f'Exported schema and {len(examples())} simulated fixtures to {DEST}')

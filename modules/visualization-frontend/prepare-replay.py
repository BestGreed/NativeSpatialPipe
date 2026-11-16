"""Measure the preserved Wwise-minute source and re-render it offline for the UI.

Only structured statistics are exported. This does not launch Wwise, a Hook,
an audio receiver, or a playback device.
"""
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'work/consumer-deps'), str(ROOT/'modules/visualization-contract')]
import numpy as np
from contract import CHANNELS, BED_TYPES, validate, validate_stream
from fixtures import spatial, coverage, layout


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def measured_meter(n, energy, peak):
    energy, peak = float(energy), float(peak)
    mean = energy/n if n else None
    return dict(sampleCount=n, sumSquares=float(energy), meanSquare=mean,
                samplePeak=float(peak) if n else None,
                rmsDbfs=10*math.log10(mean) if mean else None,
                peakDbfs=20*math.log10(peak) if peak else None,
                silent=bool(n and energy == 0), overFullScale=bool(n and peak > 1))


def packets(path):
    with path.open('rb') as stream:
        if stream.read(8) != b'NSPRAW01':
            raise ValueError('Not an NSPRAW01 capture')
        while header := stream.read(8):
            if len(header) != 8:
                raise ValueError('Truncated packet header')
            nh, nd = struct.unpack('<II', header)
            if not 0 < nh <= 65536 or not 0 < nd <= 16*1024*1024:
                raise ValueError('Invalid packet sizes')
            record, data = stream.read(nh), stream.read(nd)
            if len(record) != nh or len(data) != nd:
                raise ValueError('Truncated packet')
            yield json.loads(record), data


def prepare(archive, destination, dll):
    destination.mkdir(parents=True, exist_ok=True)
    if (destination/'manifest.json').exists():
        raise ValueError('Replay already exists; use a new output directory')
    native = module('minute_native', ROOT/'tools/endpoint-probe/Native-Roar.py')
    renderer = native.PacketRenderer(dll, 480, gain='passthrough')
    session = 'wwise-minute-source-20261014'
    route = 'wwise-minute-offline'
    states, generations, window_objects = {}, {}, {}
    chunks, chunk = [], []
    window_events, overflow = [], 0
    channel_energy, channel_peak = np.zeros(12), np.zeros(12)
    total_energy, total_peak = np.zeros(12), np.zeros(12)
    payload_hash, output_hash = hashlib.sha256(), hashlib.sha256()
    source_block, first_seq, last_seq, count = None, None, None, 0
    summaries = []
    stats = dict(creates=0, retires=0, positionUpdates=0, onsetCandidates=0,
                 eventOverflow=0, maxDynamic=0, maxStatic=0, packets=0)
    begin = time.perf_counter()

    def emit(event):
        nonlocal overflow
        if len(window_events) < 256:
            window_events.append(event)
        else:
            overflow += 1

    def event(kind, s, at_us, peak, reason=None, rise=None):
        return dict(eventId=f'{kind}-{count}-{s["key"]["slot"]}-{s["key"]["generation"]}',
                    objectKey=s['key'], kind=kind, sourceTimeUs=at_us,
                    expiresTimeUs=at_us+600000, spatial=spatial(s['position']),
                    strength=float(peak), risePerSecond=rise, endReason=reason, exactEosSample=None)

    def snapshot():
        nonlocal channel_energy, channel_peak, window_objects, window_events, overflow
        blocks = min(10, count - len(summaries)*10)
        n = blocks*480
        c = coverage(n, n)
        channels = [dict(channelId=name, band='all', meter=measured_meter(n, channel_energy[i], channel_peak[i]),
                         validDurationUs=blocks*10000) for i, name in enumerate(CHANNELS)]
        objects = []
        for slot, s in states.items():
            acc = window_objects.get((slot, s['key']['generation']))
            if acc is None:
                continue
            objects.append(dict(objectKey=s['key'], sourceIdent=s['ident'],
                                kind='dynamic' if s['type'] == 1 else 'static',
                                staticType=None if s['type'] == 1 else s['type'], spatial=spatial(s['position']),
                                meter=measured_meter(*acc), activeDurationUs=s['activeUs'],
                                applicationName=None, sourceCategory=None))
        taps = {
            'decoded-input': dict(quality='live', coverage=c, droppedBlocks=0, snapshotComplete=True,
                                  gainMode='ignored', objects=objects, channels=[]),
            'render-ready': dict(quality='live', coverage=c, droppedBlocks=0, snapshotComplete=True,
                                 gainMode='not-applicable', objects=[], channels=channels),
        }
        m = dict(schemaVersion='0.1.0', messageType='snapshot', dataOrigin='measured', routeId=route,
                 sessionId=session, streamEpoch=0, telemetrySeq=len(summaries),
                 sourceSequenceRange=dict(start=last_seq-blocks+1, endExclusive=last_seq+1),
                 audioSampleRange=dict(start=(last_seq-blocks+1)*480, endExclusive=(last_seq+1)*480),
                 sampleIndexProvenance='derived', framesPerBlock=480, sampleRate=48000,
                 layoutRevision='ideal714-v1', meterRevision='offline-linear-v1-gain-ignored-onset-v1',
                 sourceTimeUs=count*10000, publishedTimeUs=count*10000,
                 coverage={name: c for name in taps}, droppedBlocks={name: 0 for name in taps},
                 eventOverflow=overflow, taps=taps, events=window_events)
        validate(m)
        chunk.append(m)
        summaries.append(dict(sequence=m['telemetrySeq'], sourceTimeUs=m['sourceTimeUs'],
                              objects=len(objects), events=len(window_events), eventOverflow=overflow))
        stats['eventOverflow'] += overflow
        channel_energy, channel_peak = np.zeros(12), np.zeros(12)
        window_objects, window_events, overflow = {}, [], 0
        if len(chunk) == 10:
            flush_chunk()

    def flush_chunk():
        if not chunk:
            return
        filename = f'chunk-{len(chunks):03d}.json'
        raw = json.dumps(chunk, separators=(',', ':'), allow_nan=False).encode('utf-8')
        (destination/filename).write_bytes(raw)
        chunks.append(dict(file=filename, firstFrame=chunk[0]['telemetrySeq'], frames=len(chunk),
                           bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
        chunk.clear()

    try:
        for r, data in packets(archive):
            if source_block is None:
                source_block, first_seq = r['block'], r['sequence']
            if r['block'] != source_block or last_seq is not None and r['sequence'] != last_seq+1:
                raise ValueError('Multiple streams or sequence gap; do not merge')
            c = r['count']
            if r['frames'] != [480]*c or not 1 <= c <= 160 or len(data) != c*(32+480*4):
                raise ValueError('Unsupported frames/count/length')
            last_seq = r['sequence']
            props = [struct.unpack_from('<4I4f', data, i*32) for i in range(c)]
            pcm = np.frombuffer(data, dtype='<f4', offset=c*32).reshape(c,480)
            if not np.isfinite(pcm).all() or any(p[1] != 480 or p[2] not in (1, *BED_TYPES) or not all(math.isfinite(v) for v in p[4:]) for p in props):
                raise ValueError('Invalid PCM/properties')
            payload_hash.update(data)
            energy = np.sum(pcm.astype(np.float64)**2, axis=1)
            peaks = np.max(np.abs(pcm), axis=1).astype(np.float64)
            count += 1
            stats['packets'] = count
            retire = []
            for slot, prop in enumerate(props):
                ident, _, typ, flags, x, y, z, gain = prop
                s = states.get(slot)
                if s is None or s['ident'] != ident or s['type'] != typ or flags & 1:
                    if s is not None:
                        emit(event('ended', s, count*10000, peaks[slot], 'replaced'))
                        stats['retires'] += 1
                    generations[slot] = generations.get(slot, 0)+1
                    s = dict(ident=ident, type=typ, position=None, activeUs=0, priorMean=0., lastOnsetUs=-1000000,
                             key=dict(sessionId=session, streamEpoch=0, slot=slot, generation=generations[slot]))
                    states[slot] = s
                    stats['creates'] += 1
                if typ == 1 and flags & 4:
                    s['position'] = [x, y, z]
                    stats['positionUpdates'] += 1
                mean = float(energy[slot]/480)
                if mean > 1e-6:
                    s['activeUs'] += 10000
                # Engineering onset candidates, not recognized game events.
                if mean > 1e-6 and peaks[slot] > .003 and mean >= max(1e-6, s['priorMean']*4) and count*10000-s['lastOnsetUs'] >= 80000:
                    emit(event('onset', s, count*10000, peaks[slot], rise=(mean-s['priorMean'])/.01))
                    s['lastOnsetUs'] = count*10000
                    stats['onsetCandidates'] += 1
                s['priorMean'] = mean
                key = slot, s['key']['generation']
                a = window_objects.setdefault(key, [0, 0., 0.])
                a[0] += 480
                a[1] += float(energy[slot])
                a[2] = max(a[2], float(peaks[slot]))
                if flags & 8:
                    emit(event('ended', s, count*10000, peaks[slot], 'source-flag'))
                    retire.append(slot)
            for slot in list(states):
                if slot >= c or slot in retire:
                    del states[slot]
                    stats['retires'] += 1
            y = np.ctypeslib.as_array(renderer.render(r, data)).reshape(12,480)
            if not np.isfinite(y).all():
                raise ValueError('Nonfinite rendered output')
            output_hash.update(y.tobytes())
            energies = np.sum(y.astype(np.float64)**2, axis=1)
            maximum = np.max(np.abs(y), axis=1).astype(np.float64)
            channel_energy += energies
            channel_peak = np.maximum(channel_peak, maximum)
            total_energy += energies
            total_peak = np.maximum(total_peak, maximum)
            stats['maxDynamic'] = max(stats['maxDynamic'], sum(s['type'] == 1 for s in states.values()))
            stats['maxStatic'] = max(stats['maxStatic'], sum(s['type'] != 1 for s in states.values()))
            if count % 10 == 0:
                snapshot()
            if count % 1000 == 0:
                print(f'{count} packets / {count*.01:.0f}s measured', flush=True)
        if count % 10:
            snapshot()
        flush_chunk()
    finally:
        renderer.close()
    if not count:
        raise ValueError('Empty capture')
    source_manifest = json.loads((ROOT/'work/wwise-minute/sources/manifest.json').read_text(encoding='utf-8'))
    if payload_hash.hexdigest() != source_manifest['pcmPropertiesSha256']:
        raise ValueError('Payload hash differs from the preserved Wwise-minute source manifest')
    # Independently validate ordered windows from the exported files.
    def all_frames():
        for item in chunks:
            yield from json.loads((destination/item['file']).read_text(encoding='utf-8'))
    validate_stream(all_frames())
    speaker_layout = layout()
    speaker_layout.update(routeId=route, dataOrigin='measured')
    (destination/'layout.json').write_text(json.dumps(speaker_layout, allow_nan=False), encoding='utf-8')
    histories = []
    for window in (600, 3600):
        # Query from the recording start, retaining only the observed 60 seconds.
        history = dict(schemaVersion='0.1.0', messageType='history', dataOrigin='measured', routeId=route,
                       sessionId=session, streamEpoch=0, layoutRevision='ideal714-v1',
                       meterRevision='offline-linear-v1-gain-ignored-onset-v1', tap='render-ready',
                       sampleRate=48000, band='all', requestedWindowSeconds=window,
                       requestedRangeUs=dict(start=0, endExclusive=window*1000000),
                       actualRangeUs=dict(start=0, endExclusive=count*10000),
                       bucketWidthUs=1000000, boundaryErrorUs=1000000,
                       coverage=coverage(count*480, window*48000), channels=[])
        total = float(np.sum(total_energy))
        for i, name in enumerate(CHANNELS):
            e = float(total_energy[i])
            history['channels'].append(dict(channelId=name, sumSquares=e, validSamples=count*480,
                samplePeak=float(total_peak[i]), meanSquare=e/(count*480), energyIntegral=e/48000,
                contribution=e/total if total else 0))
        validate(history)
        name = f'history-{window}.json'
        (destination/name).write_text(json.dumps(history, allow_nan=False), encoding='utf-8')
        histories.append(name)
    manifest = dict(replayVersion=1, title='Wwise one-minute preserved source', dataOrigin='measured',
                    playbackKind='recorded-source-offline-rerender', frameRateHz=10, frameCount=len(summaries),
                    durationUs=count*10000, framesPerChunk=10, layout='layout.json', chunks=chunks,
                    history=dict(zip(('600', '3600'), histories)), source=dict(
                        archive=str(archive.resolve()), sourceProject='work/wwise-minute/NativeSpatialReplay',
                        pcmPropertiesSha256=payload_hash.hexdigest(), matchesWwiseSourceManifest=True,
                        firstSequence=first_seq, lastSequence=last_seq, packets=count,
                        sourceIdentIsDiagnostic=True, exactEosUnknown=True),
                    render=dict(dll=str(dll.resolve()), dllSha256=hashlib.sha256(dll.read_bytes()).hexdigest(),
                                outputPlanarBlockSha256=output_hash.hexdigest(), gain='ignored',
                                tap='render-ready', devicePlayback=False),
                    statistics=stats, processingSeconds=time.perf_counter()-begin,
                    onsetPolicy=dict(revision='onset-v1', meanSquareThreshold=1e-6,
                                     minimumPeak=.003, energyRatio=4, refractoryUs=80000, ttlUs=600000))
    (destination/'manifest.json').write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT/'build/wwise-visualization-replay')
    parser.add_argument('--dll', type=Path, default=ROOT/'build/roar-native/release/nsp_croksor.dll')
    args = parser.parse_args()
    result = prepare(args.archive, args.output, args.dll)
    print(json.dumps({k: result[k] for k in ('durationUs', 'frameCount', 'statistics', 'processingSeconds')}, indent=2))

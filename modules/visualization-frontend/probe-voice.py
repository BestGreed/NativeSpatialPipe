"""Offline speech-activity probe for preserved Wwise object tracks.

This produces candidate labels, not verified speech semantics. It never changes
the source WAVs or the measured replay chunks.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEPS = ROOT / 'build/voice-vad-deps'
sys.path.insert(0, str(DEPS))
import numpy as np  # noqa: E402
import onnxruntime as ort  # noqa: E402

MODEL = DEPS / 'silero_vad/data/silero_vad.onnx'
SOURCES = ROOT / 'work/wwise-minute/sources'
OUTPUT = ROOT / 'build/wwise-visualization-replay/voice-candidates.json'
SAMPLE_RATE = 16000
WINDOW = 512
FRAME_RATE = 10
FRAMES = 600


def probabilities(session, path):
    audio = subprocess.check_output([
        'ffmpeg', '-v', 'error', '-i', str(path), '-ac', '1', '-ar',
        str(SAMPLE_RATE), '-f', 'f32le', 'pipe:1',
    ])
    samples = np.frombuffer(audio, dtype='<f4')
    state = np.zeros((2, 1, 128), dtype=np.float32)
    context = np.zeros((1, 64), dtype=np.float32)
    rates = np.array(SAMPLE_RATE, dtype=np.int64)
    scores = []
    for start in range(0, len(samples), WINDOW):
        current = samples[start:start + WINDOW]
        if len(current) < WINDOW:
            current = np.pad(current, (0, WINDOW - len(current)))
        signal = np.concatenate((context, current.reshape(1, -1)), axis=1)
        output, state = session.run(None, {'input': signal, 'state': state, 'sr': rates})
        context = signal[:, -64:]
        scores.append(float(output[0, 0]))
    return np.asarray(scores, dtype=np.float32)


def intervals(scores):
    # Aggregate 32 ms estimates onto the replay's 100 ms clock. Two levels of
    # hysteresis reject one-shot effects; keep a short hangover for continuity.
    frame_scores = []
    for frame in range(FRAMES):
        lo = int(frame * SAMPLE_RATE / FRAME_RATE / WINDOW)
        hi = int((frame + 1) * SAMPLE_RATE / FRAME_RATE / WINDOW) + 1
        section = scores[lo:hi]
        frame_scores.append(float(np.mean(section)) if len(section) else 0.0)
    active = [False] * FRAMES
    on = False
    for i, value in enumerate(frame_scores):
        recent = frame_scores[max(0, i - 2):i + 1]
        if not on and sum(v >= .68 for v in recent) >= 2:
            on = True
        elif on and i >= 3 and max(frame_scores[i - 3:i + 1]) < .32:
            on = False
        active[i] = on
    spans = []
    start = None
    for i, value in enumerate(active + [False]):
        if value and start is None:
            start = i
        elif not value and start is not None:
            if i - start >= 4:
                spans.append([start, i])
            start = None
    return spans, frame_scores


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=128)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    args = parser.parse_args()
    if not MODEL.is_file():
        raise SystemExit('Install silero-vad==6.2.1 and onnxruntime into build/voice-vad-deps first')
    opts = ort.SessionOptions()
    opts.inter_op_num_threads = 1
    opts.intra_op_num_threads = 1
    session = ort.InferenceSession(str(MODEL), sess_options=opts, providers=['CPUExecutionProvider'])
    tracks = sorted(SOURCES.glob('Slot*.wav'))[:args.limit]
    labels = {}
    stats = {}
    for index, path in enumerate(tracks, 1):
        slot = int(path.stem.removeprefix('Slot'))
        spans, frame_scores = intervals(probabilities(session, path))
        if spans:
            labels[str(slot)] = spans
            stats[str(slot)] = {'activeFrames': sum(b - a for a, b in spans),
                                'maxFrameProbability': round(max(frame_scores), 4)}
        if index % 16 == 0 or index == len(tracks):
            print(f'processed {index}/{len(tracks)} tracks; candidate slots {len(labels)}', flush=True)
    manifest = ROOT / 'build/wwise-visualization-replay/manifest.json'
    payload = {
        'version': 1,
        'label': 'speech-candidate',
        'meaning': 'offline speech-activity hypothesis; not a verified speaker identity',
        'model': 'silero-vad 6.2.1 ONNX CPU',
        'modelSha256': hashlib.sha256(MODEL.read_bytes()).hexdigest(),
        'replayManifestSha256': hashlib.sha256(manifest.read_bytes()).hexdigest(),
        'frameRateHz': FRAME_RATE,
        'frameCount': FRAMES,
        'analyzedTracks': len(tracks),
        'intervalsBySlot': labels,
        'statsBySlot': stats,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print(f'wrote {args.output}: {len(labels)} candidate slots, '
          f'{sum(sum(b-a for a,b in spans) for spans in labels.values())} slot-frames')


if __name__ == '__main__':
    main()

"""Wire-size screening only. Does not measure realtime, CPU, IPC or GPU budgets."""
import json
from fixtures import snapshot, object_item
from contract import validate


def measure():
    rows = []
    for count in (0, 140, 160):
        m = snapshot()
        m['taps']['decoded-input']['objects'] = [object_item(i, raw=[0, 0, -1]) for i in range(count)]
        validate(m)
        size = len(json.dumps(m, separators=(',', ':'), allow_nan=False).encode('utf-8'))
        rows.append(dict(objects=count, events=0, bands='all', compactJsonBytes=size,
                         bytesPerSecondAt20Hz=size*20, bytesPerSecondAt10Hz=size*10,
                         within64KiB=size <= 65536, within500kBpsAt20Hz=size*20 <= 500000))
    return dict(dataOrigin='simulated', scope='Full-band uncompressed JSON sizing; no audio or performance test',
                status='screening-only', cases=rows)


if __name__ == '__main__':
    print(json.dumps(measure(), indent=2))

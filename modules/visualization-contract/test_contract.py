import copy
import math
import unittest

from contract import SCHEMA, validate, validate_stream, check_meter, check_position, load, loads
from fixtures import DEST, examples, snapshot, meter, history, spatial, object_item


class ContractTests(unittest.TestCase):
    def reject(self, message):
        with self.assertRaises(ValueError):
            validate(message)

    def test_export_matches_source(self):
        self.assertEqual(load(DEST/'schema.json'), SCHEMA)
        self.assertEqual(set(p.stem for p in (DEST/'fixtures').glob('*.json')), set(examples()))
        for name, expected in examples().items():
            with self.subTest(name=name):
                actual = load(DEST/'fixtures'/(name+'.json'))
                self.assertEqual(actual, expected)
                validate(actual)

    def test_rms_reference_and_weighted_gain(self):
        m = meter([math.sin(2*math.pi*1000*i/48000) for i in range(480)])
        check_meter(m)
        self.assertAlmostEqual(m['rmsDbfs'], -3.01029995664)
        m = meter([0.25]*480+[0.5]*1920)
        self.assertAlmostEqual(m['meanSquare'], 0.2125)
        check_meter(m)

    def test_missing_silence_and_overrange(self):
        for samples in ([], [0]*480, [2]*480):
            check_meter(meter(samples))
        self.assertFalse(meter([])['silent'])
        self.assertTrue(meter([0]*480)['silent'])
        self.assertGreater(meter([2]*480)['peakDbfs'], 0)

    def test_reject_bad_measurements(self):
        for field, value in [('rmsDbfs', 0), ('meanSquare', 1), ('samplePeak', 0),
                             ('silent', True), ('overFullScale', True), ('sampleCount', 0)]:
            m = snapshot()
            m['taps']['render-ready']['channels'][0]['meter'][field] = value
            with self.subTest(field=field):
                self.reject(m)

    def test_gap_and_independent_taps(self):
        m = snapshot('gap', 1)
        good = snapshot()
        for name in ('taps', 'coverage', 'droppedBlocks'):
            m[name]['decoded-input'] = good[name]['decoded-input']
        validate(m)
        m['taps']['render-ready']['snapshotComplete'] = True
        self.reject(m)
        m = snapshot('gap', 1)
        m['coverage']['render-ready']['ratio'] = 1
        self.reject(m)

    def test_reject_payload_and_unsafe_numbers(self):
        for field, value in [('pcm', [0]), ('pointer', '0x1234'), ('telemetrySeq', 2**53),
                             ('telemetrySeq', True), ('streamEpoch', -1), ('schemaVersion', '1.0.0')]:
            m = snapshot()
            m[field] = value
            self.reject(m)
        for value in (float('nan'), float('inf')):
            m = snapshot()
            m['taps']['render-ready']['channels'][0]['meter']['sumSquares'] = value
            self.reject(m)

    def test_ranges_and_channels(self):
        for mutate in [lambda m: m['audioSampleRange'].update(endExclusive=2401),
                       lambda m: m.update(publishedTimeUs=0),
                       lambda m: m['taps']['render-ready']['channels'].pop(),
                       lambda m: m['taps']['render-ready']['channels'][1].update(channelId='FL'),
                       lambda m: m['taps']['render-ready'].update(gainMode='applied')]:
            m = snapshot()
            mutate(m)
            self.reject(m)

    def test_identity_and_spatial(self):
        for raw in ([2, 0, 0], [0, 0, -2], [0, 0, 0], None):
            check_position(spatial(raw))
        p = spatial([0, 0, -2])
        self.assertEqual(p['rendererPosition'], [0, 1, 0])
        m = snapshot()
        o = object_item(raw=[0, 0, -2])
        m['taps']['decoded-input']['objects'] = [o]
        validate(m)
        o['spatial']['rendererPosition'] = [0, 0, -2]
        self.reject(m)
        o['spatial'] = spatial(None)
        o['objectKey']['streamEpoch'] = 1
        self.reject(m)

    def test_short_events_and_bounds(self):
        m = examples()['short-lifecycle']
        self.assertFalse(m['taps']['decoded-input']['objects'])
        validate(m)
        m['events'][1]['eventId'] = m['events'][0]['eventId']
        self.reject(m)
        m = examples()['short-lifecycle']
        m['events'][0]['expiresTimeUs'] = 0
        self.reject(m)
        m = snapshot()
        event = examples()['short-lifecycle']['events'][0]
        m['events'] = [dict(event, eventId=str(i)) for i in range(256)]
        m['eventOverflow'] = 10
        validate(m)
        m['events'].append(dict(event, eventId='257'))
        self.reject(m)

    def test_layout_and_history(self):
        m = examples()['layout']
        m['channels'][3]['direction'] = [0, 0, -1]
        self.reject(m)
        for window in (600, 3600):
            for seconds in (0, 2, window):
                validate(history(window, seconds))
                validate(history(window, seconds, True))
        m = history()
        m['channels'][0]['contribution'] = 0.5
        self.reject(m)
        m = history()
        m['channels'][0]['energyIntegral'] *= 2
        self.reject(m)
        m = history()
        m['actualRangeUs']['endExclusive'] = 1000000
        self.reject(m)

    def test_strict_json(self):
        for text in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}'):
            with self.assertRaises(ValueError):
                loads(text)

    def test_epoch_and_generation_sequences(self):
        first = snapshot()
        first['taps']['decoded-input']['objects'] = [object_item()]
        second = copy.deepcopy(first)
        second.update(telemetrySeq=1, sourceTimeUs=100000, publishedTimeUs=101000,
                      sourceSequenceRange=dict(start=5, endExclusive=10),
                      audioSampleRange=dict(start=2400, endExclusive=4800))
        second['taps']['decoded-input']['objects'][0]['objectKey']['generation'] = 2
        validate_stream([first, second])
        with self.assertRaises(ValueError):
            validate_stream([first, first])
        second['taps']['decoded-input']['objects'][0]['objectKey']['generation'] = 1
        second['taps']['decoded-input']['objects'][0]['sourceIdent'] = 43
        with self.assertRaises(ValueError):
            validate_stream([first, second])
        restart = snapshot()
        restart['streamEpoch'] = 1
        validate_stream([first, restart])
        with self.assertRaises(ValueError):
            validate_stream([first, restart, first])

    def test_capacity(self):
        m = snapshot()
        m['taps']['decoded-input']['objects'] = [object_item(i) for i in range(160)]
        validate(m)
        m['taps']['decoded-input']['objects'].append(object_item(0))
        self.reject(m)


if __name__ == '__main__':
    unittest.main()

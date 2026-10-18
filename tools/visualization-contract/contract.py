"""V0 offline contract. No audio, network, or third-party runtime dependencies.

SCHEMA is the source of truth; export_schema.py writes its portable JSON Schema.
validate_shape implements only the keywords emitted here, not general JSON Schema.
"""
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAX_INT = 2**53 - 1
CHANNELS = ['FL', 'FR', 'C', 'LFE', 'SL', 'SR', 'BL', 'BR', 'TFL', 'TFR', 'TBL', 'TBR']
BED_TYPES = [2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096]
BANDS = ['all', '20-120', '120-500', '500-2000', '2000-6000', '6000-12000', '12000-20000']


def obj(**properties):
    return dict(type='object', properties=properties, required=list(properties), additionalProperties=False)


def enum(*values):
    return dict(enum=list(values))


def array(items, maximum, minimum=0):
    return dict(type='array', items=items, minItems=minimum, maxItems=maximum)


def nullable(value):
    return dict(anyOf=[value, dict(type='null')])


def ref(name):
    return {'$ref': '#/$defs/' + name}


UINT = dict(type='integer', minimum=0, maximum=MAX_INT)
NUMBER = dict(type='number')
POSITIVE = dict(type='number', minimum=0)
BOOL = dict(type='boolean')
ID = dict(type='string', minLength=1, maxLength=128)
VECTOR = array(NUMBER, 3, 3)
RANGE = obj(start=UINT, endExclusive=UINT)
TAPS = ['decoded-input', 'render-ready']
QUALITY = enum('live', 'stale', 'gap', 'warmup', 'disconnected')
COVERAGE = obj(expectedSamples=UINT, validSamples=UINT,
               ratio=dict(type='number', minimum=0, maximum=1))
METER = obj(sampleCount=UINT, sumSquares=POSITIVE, meanSquare=nullable(POSITIVE),
            samplePeak=nullable(POSITIVE), rmsDbfs=nullable(NUMBER), peakDbfs=nullable(NUMBER),
            silent=BOOL, overFullScale=BOOL)
POSITION = obj(position=nullable(VECTOR), positionValid=BOOL,
               coordinateSystem=enum('listener-relative-x-right-y-up-z-back'),
               provenance=enum('raw'), units=enum('source-unverified'),
               rendererPosition=nullable(VECTOR), rendererTransform=enum('normalize-x-negz-y'))
IDENTITY = obj(sessionId=ID, streamEpoch=UINT, slot=dict(type='integer', minimum=0, maximum=159),
               generation=dict(type='integer', minimum=1, maximum=MAX_INT))
OBJECT = obj(objectKey=ref('identity'), sourceIdent=dict(type='integer', minimum=0, maximum=2**32-1),
             kind=enum('dynamic', 'static'), staticType=nullable(enum(*BED_TYPES)),
             spatial=ref('position'), meter=ref('meter'), activeDurationUs=UINT,
             applicationName=nullable(ID), sourceCategory=nullable(ID))
EVENT = obj(eventId=ID, objectKey=ref('identity'), kind=enum('onset', 'ended', 'invalidated'),
            sourceTimeUs=UINT, expiresTimeUs=UINT, spatial=ref('position'),
            strength=POSITIVE, risePerSecond=nullable(NUMBER),
            endReason=nullable(enum('source-flag', 'replaced', 'complete-snapshot', 'epoch-change')),
            exactEosSample=enum(None))
CHANNEL = obj(channelId=enum(*CHANNELS), band=enum('all'), meter=ref('meter'),
              validDurationUs=UINT)
TAP = obj(quality=QUALITY, coverage=ref('coverage'), droppedBlocks=UINT,
          snapshotComplete=BOOL, gainMode=enum('applied', 'ignored', 'not-applicable'),
          objects=array(ref('object'), 160), channels=array(ref('channel'), 84))
VERSIONS = enum('0.1.0', '0.2.0')
ENVELOPE = dict(schemaVersion=VERSIONS, dataOrigin=enum('simulated', 'measured'),
                routeId=ID, sessionId=ID, streamEpoch=UINT, telemetrySeq=UINT,
                sourceSequenceRange=RANGE, audioSampleRange=RANGE,
                sampleIndexProvenance=enum('derived'), framesPerBlock=enum(480), sampleRate=enum(48000),
                layoutRevision=ID, meterRevision=ID, sourceTimeUs=UINT, publishedTimeUs=UINT,
                coverage=obj(**{tap: ref('coverage') for tap in TAPS}),
                droppedBlocks=obj(**{tap: UINT for tap in TAPS}), eventOverflow=UINT)
SNAPSHOT = obj(**ENVELOPE, messageType=enum('snapshot'),
               taps=obj(**{tap: ref('tap') for tap in TAPS}), events=array(ref('event'), 256))
LAYOUT = obj(schemaVersion=VERSIONS, messageType=enum('layout'), dataOrigin=enum('simulated', 'measured'),
             routeId=ID, layoutRevision=ID, layoutId=enum('roar-layout714'),
             coordinateSystem=enum('listener-relative-x-right-y-up-z-back'),
             positionSource=enum('idealized'),
             channels=array(obj(channelId=enum(*CHANNELS), staticType=enum(*BED_TYPES),
                                direction=nullable(VECTOR)), 12, 12))
CAPABILITIES = obj(schemaVersion=VERSIONS, messageType=enum('capabilities'),
                   dataOrigin=enum('simulated', 'measured'),
                   routes=array(obj(routeId=ID, authority=enum('fsa', 'fixture', 'native'),
                                    soundNavigation=BOOL, soundSpectrum=BOOL,
                                    decodedInput=BOOL, renderReady=BOOL, frequencyBands=enum(False),
                                    history=BOOL, lufs=enum(False), truePeak=enum(False),
                                    devicePlayback=enum(False), control=enum(False),
                                    maxObjects=enum(160), maxChannels=enum(12)), 16),
                   publishRatesHz=array(enum(1, 10, 20), 3, 1),
                   historyWindowsSeconds=array(enum(600, 3600), 2, 1))
SUBSCRIPTION = obj(schemaVersion=VERSIONS, messageType=enum('subscribe', 'negotiated'),
                   routeId=ID, mode=enum('sound-navigation', 'sound-spectrum', 'both'),
                   publishRateHz=enum(1, 10, 20), bands=array(enum('all'), 1, 1))
HISTORY = obj(schemaVersion=VERSIONS, messageType=enum('history'),
              dataOrigin=enum('simulated', 'measured'), routeId=ID, sessionId=ID,
              streamEpoch=UINT, layoutRevision=ID, meterRevision=ID,
              tap=enum('render-ready'), sampleRate=enum(48000), band=enum('all'),
              requestedWindowSeconds=enum(600, 3600), requestedRangeUs=RANGE,
              actualRangeUs=nullable(RANGE), bucketWidthUs=enum(1000000),
              boundaryErrorUs=enum(1000000), coverage=ref('coverage'),
              channels=array(obj(channelId=enum(*CHANNELS), sumSquares=POSITIVE,
                                 validSamples=UINT, samplePeak=nullable(POSITIVE),
                                 meanSquare=nullable(POSITIVE), energyIntegral=POSITIVE,
                                 contribution=dict(type='number', minimum=0, maximum=1)), 12, 12))
SCHEMA = {'$schema': 'https://json-schema.org/draft/2020-12/schema',
          'title': 'NSp visualization V0.1 (offline contract)',
          '$defs': dict(coverage=COVERAGE, meter=METER, identity=IDENTITY, position=POSITION,
                        object=OBJECT, event=EVENT, channel=CHANNEL, tap=TAP),
          'oneOf': [SNAPSHOT, LAYOUT, CAPABILITIES, SUBSCRIPTION, HISTORY]}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def close(a, b):
    return math.isclose(a, b, rel_tol=1e-8, abs_tol=1e-10)


def _validate_shape_interpreted(value, schema, path='$'):
    """Strict validator for this module's schema vocabulary only; fail on unknown keywords."""
    supported = {'$ref', '$schema', '$defs', 'title', 'oneOf', 'anyOf', 'type', 'enum',
                 'minimum', 'maximum', 'minLength', 'maxLength', 'items', 'minItems',
                 'maxItems', 'properties', 'required', 'additionalProperties'}
    require(not set(schema) - supported, f'{path}: unsupported schema keyword')
    if '$ref' in schema:
        return _validate_shape_interpreted(value, SCHEMA['$defs'][schema['$ref'].split('/')[-1]], path)
    for key in ('oneOf', 'anyOf'):
        if key in schema:
            successes = 0
            for option in schema[key]:
                try:
                    _validate_shape_interpreted(value, option, path)
                    successes += 1
                except ValueError:
                    pass
            require(successes == 1 if key == 'oneOf' else successes > 0, f'{path}: no unique valid {key} variant')
            return
    if 'enum' in schema:
        require(any(type(value) is type(v) and value == v for v in schema['enum']), f'{path}: invalid enum')
    kind = schema.get('type')
    checks = {'object': lambda: type(value) is dict, 'array': lambda: type(value) is list,
              'integer': lambda: type(value) is int, 'number': lambda: type(value) in (int, float) and math.isfinite(value),
              'string': lambda: type(value) is str, 'boolean': lambda: type(value) is bool,
              'null': lambda: value is None}
    if kind:
        require(checks[kind](), f'{path}: expected {kind}')
    for key, compare in [('minimum', lambda a, b: a >= b), ('maximum', lambda a, b: a <= b)]:
        if key in schema:
            require(compare(value, schema[key]), f'{path}: {key}')
    for key, compare in [('minItems', lambda a, b: a >= b), ('maxItems', lambda a, b: a <= b),
                         ('minLength', lambda a, b: a >= b), ('maxLength', lambda a, b: a <= b)]:
        if key in schema:
            require(compare(len(value), schema[key]), f'{path}: {key}')
    if kind == 'object':
        require(set(schema['required']) <= set(value), f'{path}: missing fields')
        require(set(value) <= set(schema['properties']), f'{path}: unknown fields (PCM/addresses forbidden)')
        for key, item in value.items():
            _validate_shape_interpreted(item, schema['properties'][key], path + '.' + key)
    if kind == 'array':
        for i, item in enumerate(value):
            _validate_shape_interpreted(item, schema['items'], f'{path}[{i}]')


def _compile_shape(root):
    """Prebind the immutable built-in schema; retain every shape constraint.

    Avoid constructing keyword sets, lambdas, and error-path strings for every
    scalar in every live snapshot. Invalid input uses the interpreter below to
    preserve the original detailed diagnostics.
    """
    cache={}
    supported={'$ref','$schema','$defs','title','oneOf','anyOf','type','enum',
               'minimum','maximum','minLength','maxLength','items','minItems',
               'maxItems','properties','required','additionalProperties'}
    def compile_node(schema):
        if id(schema) in cache:return cache[id(schema)]
        require(not set(schema)-supported,'unsupported schema keyword')
        if '$ref' in schema:
            return compile_node(root['$defs'][schema['$ref'].split('/')[-1]])
        union='oneOf' if 'oneOf' in schema else 'anyOf' if 'anyOf' in schema else None
        if union:
            options=tuple(compile_node(option) for option in schema[union])
            def check_union(value):
                successes=0
                for option in options:
                    try:option(value)
                    except ValueError:continue
                    successes+=1
                    if union=='anyOf':return
                if successes!=1:raise ValueError('union')
            cache[id(schema)]=check_union
            return check_union
        kind=schema.get('type')
        enum_values=tuple((type(v),v) for v in schema['enum']) if 'enum' in schema else None
        minimum=schema.get('minimum');maximum=schema.get('maximum')
        min_length=schema.get('minItems',schema.get('minLength'))
        max_length=schema.get('maxItems',schema.get('maxLength'))
        properties={key:compile_node(item) for key,item in schema.get('properties',{}).items()}
        required=frozenset(schema.get('required',()))
        allowed=frozenset(properties)
        items=compile_node(schema['items']) if 'items' in schema else None
        def check(value):
            typ=type(value)
            if enum_values is not None:
                if not any(typ is expected and value==candidate for expected,candidate in enum_values):raise ValueError('enum')
            if kind=='object':
                if typ is not dict or not required<=value.keys() or not value.keys()<=allowed:raise ValueError('object')
                for key,item in value.items():properties[key](item)
            elif kind=='array':
                if typ is not list:raise ValueError('array')
                if min_length is not None and len(value)<min_length:raise ValueError('min length')
                if max_length is not None and len(value)>max_length:raise ValueError('max length')
                for item in value:items(item)
            elif kind=='number':
                if typ not in (int,float) or not math.isfinite(value):raise ValueError('number')
            elif kind=='integer':
                if typ is not int:raise ValueError('integer')
            elif kind=='string':
                if typ is not str:raise ValueError('string')
            elif kind=='boolean':
                if typ is not bool:raise ValueError('boolean')
            elif kind=='null':
                if value is not None:raise ValueError('null')
            elif kind:raise ValueError('unsupported type')
            if minimum is not None and value<minimum:raise ValueError('minimum')
            if maximum is not None and value>maximum:raise ValueError('maximum')
            if min_length is not None and len(value)<min_length:raise ValueError('min length')
            if max_length is not None and len(value)>max_length:raise ValueError('max length')
        cache[id(schema)]=check
        return check
    return compile_node(root)


_compiled_shape=None


def validate_shape(value, schema, path='$'):
    global _compiled_shape
    if schema is not SCHEMA:
        return _validate_shape_interpreted(value,schema,path)
    if _compiled_shape is None:_compiled_shape=_compile_shape(SCHEMA)
    try:
        _compiled_shape(value)
    except (ValueError,TypeError,KeyError,OverflowError):
        return _validate_shape_interpreted(value,schema,path)


def check_coverage(c):
    require(c['validSamples'] <= c['expectedSamples'], 'coverage: valid exceeds expected')
    ratio = c['validSamples'] / c['expectedSamples'] if c['expectedSamples'] else 0
    require(close(c['ratio'], ratio), 'coverage: wrong ratio')


def check_meter(m):
    n, energy, peak = m['sampleCount'], m['sumSquares'], m['samplePeak']
    if n == 0:
        require(energy == 0 and all(m[k] is None for k in ('meanSquare', 'samplePeak', 'rmsDbfs', 'peakDbfs')),
                'missing samples must have null measurements')
        require(not m['silent'] and not m['overFullScale'], 'missing is not silence')
        return
    require(peak is not None and m['meanSquare'] is not None, 'observed meter needs linear values')
    require(close(m['meanSquare'], energy / n), 'meanSquare is sample-weighted')
    require(peak * peak <= energy + 1e-8 and energy <= n * peak * peak + 1e-8, 'peak/energy inconsistency')
    require((energy == 0) == (peak == 0) == m['silent'], 'silence inconsistency')
    require(m['overFullScale'] == (peak > 1), 'over-full-scale must not be clipped')
    for field, linear, factor in [('rmsDbfs', energy / n, 10), ('peakDbfs', peak, 20)]:
        require(m[field] is None if linear == 0 else m[field] is not None and close(m[field], factor * math.log10(linear)),
                field + ': wrong dBFS reference')


def check_position(p):
    raw, rendered = p['position'], p['rendererPosition']
    require(p['positionValid'] == (raw is not None), 'position validity mismatch')
    if raw is None:
        require(rendered is None, 'unknown position cannot have renderer position')
    else:
        radius = math.sqrt(sum(v*v for v in raw))
        scale = 1 / radius if radius > 0 else 1
        expected = [raw[0]*scale, -raw[2]*scale, raw[1]*scale]
        require(rendered is not None and all(close(a, b) for a, b in zip(rendered, expected)), 'renderer transform mismatch')


def validate(message):
    validate_shape(message, SCHEMA)
    kind = message['messageType']
    if kind in ('subscribe', 'negotiated'):
        require(len(set(message['bands'])) == len(message['bands']), 'duplicate band')
    elif kind == 'capabilities':
        ids = [r['routeId'] for r in message['routes']]
        require(len(ids) == len(set(ids)), 'duplicate route')
        for route in message['routes']:
            require(route['authority'] != 'native' or message['schemaVersion'] == '0.2.0', 'native authority requires 0.2.0')
            require(not route['soundNavigation'] or route['decodedInput'], 'navigation needs input tap')
            require(not route['soundSpectrum'] or route['renderReady'], 'spectrum needs output tap')
    elif kind == 'layout':
        require([c['channelId'] for c in message['channels']] == CHANNELS, 'ROAR channel order mismatch')
        require([c['staticType'] for c in message['channels']] == BED_TYPES, 'static type mapping mismatch')
        for c in message['channels']:
            d = c['direction']
            require(d is None if c['channelId'] == 'LFE' else d is not None and close(sum(v*v for v in d), 1),
                    'LFE has no direction; other idealized directions must be unit vectors')
    elif kind == 'snapshot':
        check_snapshot(message)
    elif kind == 'history':
        check_history(message)
    return message


def check_snapshot(m):
    seq, samples = m['sourceSequenceRange'], m['audioSampleRange']
    require(seq['start'] < seq['endExclusive'], 'empty/reversed source range')
    require(samples == {k: v * 480 for k, v in seq.items()}, 'derived sample range mismatch')
    require(m['sourceTimeUs'] <= m['publishedTimeUs'], 'publication precedes source')
    expected = samples['endExclusive'] - samples['start']
    for name, tap in m['taps'].items():
        c = tap['coverage']
        check_coverage(c)
        require(c['expectedSamples'] == expected, 'tap expected sample range mismatch')
        require(c['validSamples'] % 480 == 0, 'V0 observes complete blocks')
        require(tap['droppedBlocks'] * 480 == expected - c['validSamples'], 'tap drop count mismatch')
        require(c == m['coverage'][name] and tap['droppedBlocks'] == m['droppedBlocks'][name], 'envelope tap mismatch')
        if tap['quality'] == 'live':
            require(c['ratio'] == 1, 'live requires complete coverage')
        if tap['snapshotComplete']:
            require(tap['quality'] == 'live' and c['ratio'] == 1, 'gap/stale cannot imply object EOS')
        require(tap['gainMode'] == 'not-applicable' if name == 'render-ready' else tap['gainMode'] in ('applied', 'ignored'), 'gain mode/tap mismatch')
        require(not tap['objects'] if name == 'render-ready' else not tap['channels'], 'wrong tap payload')
        seen = set()
        for item in tap['objects']:
            identity = item['objectKey']
            require(identity['sessionId'] == m['sessionId'] and identity['streamEpoch'] == m['streamEpoch'], 'object session/epoch mismatch')
            require(identity['slot'] not in seen, 'duplicate active slot')
            seen.add(identity['slot'])
            require((item['kind'] == 'dynamic') == (item['staticType'] is None), 'static type/kind mismatch')
            check_position(item['spatial'])
            check_meter(item['meter'])
            require(item['meter']['sampleCount'] <= c['validSamples'], 'object exceeds observed samples')
        seen = set()
        for item in tap['channels']:
            key = (item['channelId'], item['band'])
            require(key not in seen, 'duplicate channel/band')
            seen.add(key)
            require(item['band'] == 'all', 'V0 executable metering supports full-band only')
            check_meter(item['meter'])
            require(item['meter']['sampleCount'] == c['validSamples'], 'channel sample count mismatch')
            require(item['validDurationUs'] * 48000 == c['validSamples'] * 1000000, 'valid duration mismatch')
        if name == 'render-ready':
            require({c['channelId'] for c in tap['channels']} == set(CHANNELS), 'output must explicitly include all channels, including unknown meters')
    event_ids = set()
    for event in m['events']:
        require(event['eventId'] not in event_ids, 'duplicate event ID')
        event_ids.add(event['eventId'])
        require(event['sourceTimeUs'] < event['expiresTimeUs'], 'invalid event TTL')
        require(event['sourceTimeUs'] <= m['sourceTimeUs'], 'future event')
        require(event['objectKey']['sessionId'] == m['sessionId'] and event['objectKey']['streamEpoch'] == m['streamEpoch'], 'event session/epoch mismatch')
        require(event['endReason'] is None if event['kind'] == 'onset' else event['endReason'] is not None, 'event end reason mismatch')
        check_position(event['spatial'])


def check_history(m):
    check_coverage(m['coverage'])
    r = m['requestedRangeUs']
    require(r['endExclusive'] - r['start'] == m['requestedWindowSeconds'] * 1000000, 'history window mismatch')
    require(m['coverage']['expectedSamples'] == m['requestedWindowSeconds'] * 48000, 'history expected samples mismatch')
    a = m['actualRangeUs']
    require(a is not None or m['coverage']['validSamples'] == 0, 'history coverage without actual range')
    if a:
        require(r['start'] <= a['start'] < a['endExclusive'] <= r['endExclusive'], 'history range outside request')
        require(m['coverage']['validSamples'] <= (a['endExclusive'] - a['start']) * 48000 / 1000000, 'history samples exceed actual range')
    require([c['channelId'] for c in m['channels']] == CHANNELS, 'history channel order mismatch')
    total = sum(c['sumSquares'] for c in m['channels'])
    for c in m['channels']:
        n, energy = c['validSamples'], c['sumSquares']
        require(n == m['coverage']['validSamples'], 'history coverage mismatch')
        require(c['meanSquare'] is None if not n else c['meanSquare'] is not None and close(c['meanSquare'], energy / n), 'history mean square mismatch')
        require(close(c['energyIntegral'], energy / 48000), 'history integral mismatch')
        require(close(c['contribution'], energy / total if total else 0), 'history contribution mismatch')
        if not n:
            require(energy == 0 and c['samplePeak'] is None, 'missing history is not silence')
        else:
            p = c['samplePeak']
            require(p is not None and p*p <= energy + 1e-8 and energy <= n*p*p + 1e-8, 'history peak mismatch')


def loads(text):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate JSON key: ' + key)
            result[key] = value
        return result
    def bad_constant(value):
        raise ValueError('nonfinite JSON: ' + value)
    return json.loads(text, object_pairs_hook=pairs, parse_constant=bad_constant)


def load(path):
    return loads(Path(path).read_text(encoding='utf-8'))


def validate_stream(messages):
    """Validate one route's ordered snapshots; never infer EOS across a gap."""
    previous = None
    retired_scopes = set()
    generations = {}
    for m in messages:
        validate(m)
        require(m['messageType'] == 'snapshot', 'stream requires snapshots')
        scope = (m['sessionId'], m['streamEpoch'])
        if previous is not None:
            require(m['routeId'] == previous['routeId'], 'stream route changed')
            if m['sessionId'] == previous['sessionId']:
                require(m['sourceTimeUs'] >= previous['sourceTimeUs'], 'session source time rewound')
                require(m['publishedTimeUs'] >= previous['publishedTimeUs'], 'session publication time rewound')
            old_scope = (previous['sessionId'], previous['streamEpoch'])
            if scope != old_scope:
                retired_scopes.add(old_scope)
                require(scope not in retired_scopes, 'retired epoch replay')
                generations.clear()
            else:
                require(m['telemetrySeq'] > previous['telemetrySeq'], 'telemetry sequence must increase')
                require(m['sourceSequenceRange']['start'] >= previous['sourceSequenceRange']['endExclusive'], 'source windows overlap or rewind')
                require(m['sourceTimeUs'] >= previous['sourceTimeUs'], 'source time rewound')
                require(m['publishedTimeUs'] >= previous['publishedTimeUs'], 'publication time rewound')
        for o in m['taps']['decoded-input']['objects']:
            key = o['objectKey']
            slot, gen = key['slot'], key['generation']
            if slot in generations:
                old_gen, ident, typ = generations[slot]
                require(gen >= old_gen, 'slot generation rewound')
                require(gen != old_gen or (ident, typ) == (o['sourceIdent'], o['staticType']), 'identity changed without new generation')
            generations[slot] = (gen, o['sourceIdent'], o['staticType'])
        previous = m


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Validate V0 shape and measurement semantics offline')
    parser.add_argument('files', nargs='+', type=Path)
    args = parser.parse_args()
    for path in args.files:
        validate(load(path))
        print('PASS', path)

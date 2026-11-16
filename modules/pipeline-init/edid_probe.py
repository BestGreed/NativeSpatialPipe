"""Read-only EDID audio evidence probe. Python stdlib only; never changes endpoints.

Bit interpretations cross-checked against v4l-utils utils/edid-decode/parse-cta-block.cpp.
This is a bounded audio subset parser, not a CTA/EDID conformance validator.
"""
import argparse
import ctypes
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

FORMATS = {1: 'LPCM', 2: 'AC-3', 3: 'MPEG1', 4: 'MP3', 5: 'MPEG2',
           6: 'AAC LC', 7: 'DTS', 8: 'ATRAC', 9: 'One Bit Audio',
           10: 'E-AC-3', 11: 'DTS-HD', 12: 'MAT/MLP', 13: 'DST', 14: 'WMA Pro'}
EXT_FORMATS = {11: 'MPEG-H 3D Audio', 12: 'AC-4', 13: 'LPCM 3D Audio', 14: 'Auro-Cx'}
RATES = [32000, 44100, 48000, 88200, 96000, 176400, 192000]
SPEAKERS = ['FL/FR', 'LFE1', 'FC', 'LS/RS', 'BC', 'FLc/FRc', 'BL/BR',
            'FLw/FRw', 'TpFL/TpFR', 'TpC', 'TpFC', 'LS/RS(deprecated)',
            'LFE2', 'TpBC', 'SiL/SiR', 'TpSiL/TpSiR', 'TpBL/TpBR',
            'BtFC', 'BtFL/BtFR', 'TpLS/TpRS']


def parse_sad(raw, offset):
    a, b, c = raw
    fmt = (a >> 3) & 15
    ext = c >> 3 if fmt == 15 else None
    r = dict(offset=offset, raw=raw.hex(), format_code=fmt,
             format=EXT_FORMATS.get(ext, f'Extended {ext}') if fmt == 15
             else FORMATS.get(fmt, f'Reserved {fmt}'),
             sample_rates_hz=[rate for bit, rate in enumerate(RATES) if b & (1 << bit)],
             dependent_byte=c)
    # Extended formats use these bits for levels/profiles as well as channels.
    if fmt != 15:
        r['max_channels'] = (a & 7) + 1
    else:
        r['extended_format_code'] = ext
    if fmt == 1:
        r['sample_sizes_bits'] = [n for bit, n in enumerate([16, 20, 24]) if c & (1 << bit)]
    elif 2 <= fmt <= 8:
        r['max_bitrate_kbps'] = c * 8
    elif fmt == 10:
        r.update(joc=bool(c & 1), joc_acmod28=bool(c & 2))
    elif fmt == 12:
        r.update(mat_object_audio_pcm=bool(c & 1),
                 mat_hash_not_required=bool(c & 2) if c & 1 else None)
    elif fmt == 11:
        # edid-decode explicitly labels this interpretation as reverse engineering.
        r['dts_x_reverse_engineered_hint'] = bool(c & 2)
    return r


def parse_edid(data):
    errors, warnings, blocks, sads, dolby = [], [], [], [], []
    result = dict(sha256=hashlib.sha256(data).hexdigest(), length=len(data),
                  errors=errors, warnings=warnings, blocks=blocks, audio_descriptors=sads,
                  dolby_audio_blocks=dolby)
    if len(data) < 128:
        errors.append('Base block truncated')
        result.update(structure_ok=False, advertised={})
        return result
    if data[:8] != bytes.fromhex('00ffffffffffff00'):
        errors.append('Invalid EDID header')
    expected = (data[126] + 1) * 128
    if len(data) != expected:
        errors.append(f'Extension count requires {expected} bytes; received {len(data)}')
    ident = int.from_bytes(data[8:10], 'big')
    result['manufacturer'] = ''.join(chr(64 + ((ident >> shift) & 31)) for shift in (10, 5, 0))
    result['product_code'] = int.from_bytes(data[10:12], 'little')
    result['monitor_name'] = None
    for pos in range(54, 126, 18):
        if data[pos:pos+5] == b'\x00\x00\x00\xfc\x00':
            result['monitor_name'] = data[pos+5:pos+18].decode('ascii', 'replace').strip('\x00\n ')
    basic_audio = False
    # Do not parse undeclared trailing blocks as capability evidence.
    for index in range(min(len(data) // 128, data[126] + 1)):
        raw = data[index*128:(index+1)*128]
        block = dict(index=index, tag=raw[0], checksum_ok=sum(raw) % 256 == 0,
                     raw=raw.hex(), data_blocks=[])
        blocks.append(block)
        if not block['checksum_ok']:
            errors.append(f'Block {index}: bad checksum')
        if index == 0:
            continue
        if raw[0] != 2:
            warnings.append(f'Block {index}: extension 0x{raw[0]:02x} retained but not decoded')
            continue
        block['cta_revision'] = raw[1]
        block['basic_audio'] = bool(raw[3] & 64)
        basic_audio |= block['basic_audio']
        if raw[1] < 3:
            warnings.append(f'Block {index}: CTA revision below 3 not decoded')
            continue
        end = raw[2]
        if end == 0:
            continue  # No DTDs or Data Block Collection.
        if not 4 <= end <= 127:
            errors.append(f'Block {index}: invalid DTD offset {end}')
            continue
        pos = 4
        while pos < end:
            header = raw[pos]
            if header == 0:
                if any(raw[pos:end]):
                    errors.append(f'Block {index}: nonzero data after padding')
                break
            length, tag = header & 31, header >> 5
            stop = pos + 1 + length
            if stop > end:
                errors.append(f'Block {index}: data block crosses DTD offset at {pos}')
                break
            payload = raw[pos+1:stop]
            item = dict(tag=tag, offset=index*128+pos, raw=payload.hex())
            block['data_blocks'].append(item)
            if tag == 1:
                if not length or length % 3:
                    errors.append(f'Block {index}: invalid audio descriptor length {length}')
                for k in range(0, length - 2, 3):
                    sads.append(parse_sad(payload[k:k+3], index*128+pos+1+k))
            elif tag == 4:
                if length != 3:
                    errors.append(f'Block {index}: invalid speaker allocation length')
                else:
                    mask = int.from_bytes(payload, 'little')
                    item['speaker_allocation'] = [n for bit, n in enumerate(SPEAKERS) if mask & (1 << bit)]
            elif tag == 3 and length >= 3:
                item['oui'] = f'{int.from_bytes(payload[:3], "little"):06x}'
            elif tag == 7:
                if not length:
                    errors.append(f'Block {index}: missing extended tag')
                else:
                    item['extended_tag'] = payload[0]
                    if payload[0] == 0x11 and length >= 4:
                        item['oui'] = f'{int.from_bytes(payload[1:4], "little"):06x}'
                        if item['oui'] == '00d046':
                            if length < 6:
                                errors.append(f'Block {index}: truncated Dolby audio block')
                            else:
                                flags, extra = payload[4:6]
                                dolby.append(dict(offset=item['offset'], version=1+(flags & 7),
                                    headphone_only=bool(flags & 128), height_zone=bool(flags & 64),
                                    surround_zone=bool(flags & 32), center_zone=bool(flags & 16),
                                    mat_pcm_48khz_only_no_truehd=bool(extra & 1)))
            pos = stop
    result['structure_ok'] = not errors
    # All summary booleans mean advertised evidence only. A damaged capture is unknown.
    def evidence(predicate):
        return None if errors else any(predicate(s) for s in sads)
    result['advertised'] = dict(
        basic_audio=None if errors else basic_audio,
        lpcm_max_channels=None if errors else max([s['max_channels'] for s in sads if s['format_code'] == 1]
                                                + ([2] if basic_audio else [0])),
        eac3=evidence(lambda s: s['format_code'] == 10),
        eac3_joc=evidence(lambda s: s['format_code'] == 10 and (s['joc'] or s['joc_acmod28'])),
        mat_mlp=evidence(lambda s: s['format_code'] == 12),
        mat_object_audio_pcm=evidence(lambda s: s['format_code'] == 12 and s['mat_object_audio_pcm']),
        dts_hd=evidence(lambda s: s['format_code'] == 11),
        dts_x_reverse_engineered_hint=evidence(lambda s: s.get('dts_x_reverse_engineered_hint', False)))
    result['limitations'] = ['EDID advertisement only; no Windows provider, codec license, playback or physical speaker proof',
        'Absent flag means not found in decoded blocks, not proof of no support',
        'DisplayID, HDMI Audio, Room Configuration and Speaker Location blocks retained raw, not decoded',
        'Dolby video vendor blocks are not Dolby audio evidence; MAT/MLP alone does not prove Atmos over TrueHD']
    return result


def display_snapshot():
    from ctypes import wintypes as w
    class Display(ctypes.Structure):
        _fields_ = [('cb', w.DWORD), ('name', w.WCHAR*32), ('description', w.WCHAR*128),
                    ('flags', w.DWORD), ('id', w.WCHAR*128), ('key', w.WCHAR*128)]
    enum = ctypes.WinDLL('user32', use_last_error=True).EnumDisplayDevicesW
    enum.argtypes = [w.LPCWSTR, w.DWORD, ctypes.POINTER(Display), w.DWORD]
    enum.restype = w.BOOL
    monitors = []
    i = 0
    while True:
        adapter = Display(); adapter.cb = ctypes.sizeof(adapter)
        if not enum(None, i, ctypes.byref(adapter), 0):
            break
        i += 1
        j = 0
        while True:
            monitor = Display(); monitor.cb = ctypes.sizeof(monitor)
            if not enum(adapter.name, j, ctypes.byref(monitor), 1):
                break
            j += 1
            parts = monitor.id.split('#')
            monitors.append(dict(adapter=adapter.name, adapter_flags=adapter.flags,
                monitor_flags=monitor.flags, interface=monitor.id,
                registry_identity='\\'.join(parts[1:3]).lower() if len(parts) >= 4 else None,
                active_in_desktop=bool(adapter.flags & 1 and monitor.flags & 1)))
    return monitors


def collect_windows(output):
    import winreg as reg
    output.mkdir(parents=True, exist_ok=False)
    monitors = display_snapshot()
    records, errors = [], []
    root = r'SYSTEM\CurrentControlSet\Enum\DISPLAY'
    def subkeys(key):
        return [reg.EnumKey(key, i) for i in range(reg.QueryInfoKey(key)[0])]
    with reg.OpenKey(reg.HKEY_LOCAL_MACHINE, root) as display:
        for model in subkeys(display):
            with reg.OpenKey(display, model) as mk:
                for instance in subkeys(mk):
                    identity = model + '\\' + instance
                    try:
                        with reg.OpenKey(mk, instance + r'\Device Parameters') as params:
                            data, kind = reg.QueryValueEx(params, 'EDID')
                            if kind != reg.REG_BINARY:
                                raise ValueError('EDID is not REG_BINARY')
                            overrides = {}
                            try:
                                with reg.OpenKey(params, 'EDID_OVERRIDE') as ov:
                                    for i in range(reg.QueryInfoKey(ov)[1]):
                                        name, val, typ = reg.EnumValue(ov, i)
                                        overrides[name] = val.hex() if isinstance(val, bytes) else val
                            except FileNotFoundError:
                                pass
                        name = f'{len(records):02d}-{model}.bin'
                        (output / name).write_bytes(data)
                        matching = [m for m in monitors if m['registry_identity'] == identity.lower()]
                        records.append(dict(identity=identity, source='registry_cached_edid', raw_file=name,
                            desktop_matches=matching, active_in_desktop=any(m['active_in_desktop'] for m in matching),
                            physical_transport='unknown; not queried', registry_overrides=overrides,
                            effective_edid_verified=False, analysis=parse_edid(data)))
                    except (OSError, ValueError) as exc:
                        errors.append(dict(identity=identity, error=str(exc)))
    report = dict(captured_utc=datetime.now(timezone.utc).isoformat(),
                  scope='Read-only cached EDID and GDI desktop identity snapshot; no endpoint configuration or audio playback',
                  display_snapshot=monitors, records=records, collection_errors=errors)
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    scan = sub.add_parser('scan'); scan.add_argument('output', type=Path)
    parse = sub.add_parser('parse'); parse.add_argument('input', type=Path)
    a = p.parse_args()
    if a.command == 'scan':
        report = collect_windows(a.output)
        print(json.dumps([dict(identity=r['identity'], active=r['active_in_desktop'],
            name=r['analysis'].get('monitor_name'), structure_ok=r['analysis']['structure_ok'],
            advertised=r['analysis']['advertised']) for r in report['records']], ensure_ascii=False, indent=2))
    else:
        print(json.dumps(parse_edid(a.input.read_bytes()), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

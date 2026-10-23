import unittest
from edid_probe import parse_edid


def checksum(block):
    block[127] = (-sum(block[:127])) & 255
    return bytes(block)


def db(tag, payload):
    return bytes([(tag << 5) | len(payload)]) + payload


def fixture(collection=b'', basic=True, extra=b''):
    base = bytearray(128)
    base[:8] = bytes.fromhex('00ffffffffffff00')
    base[126] = 1 + bool(extra)
    cta = bytearray(128)
    cta[:4] = bytes([2, 3, 4+len(collection), 64 if basic else 0])
    cta[4:4+len(collection)] = collection
    return checksum(base) + checksum(cta) + extra


class EdidTests(unittest.TestCase):
    def test_stereo_does_not_imply_object_audio(self):
        r = parse_edid(fixture(db(1, bytes.fromhex('090707'))))
        self.assertTrue(r['structure_ok'])
        self.assertEqual(r['advertised']['lpcm_max_channels'], 2)
        self.assertFalse(r['advertised']['mat_object_audio_pcm'])

    def test_basic_audio_without_sad(self):
        self.assertEqual(parse_edid(fixture())['advertised']['lpcm_max_channels'], 2)

    def test_truehd_only_does_not_imply_mat_objects(self):
        r = parse_edid(fixture(db(1, bytes.fromhex('677e00'))))
        self.assertTrue(r['advertised']['mat_mlp'])
        self.assertFalse(r['advertised']['mat_object_audio_pcm'])

    def test_joc_and_mat_with_exact_offsets(self):
        r = parse_edid(fixture(db(1, bytes.fromhex('570703677e03'))))
        self.assertTrue(r['advertised']['eac3_joc'])
        self.assertTrue(r['advertised']['mat_object_audio_pcm'])
        self.assertEqual(r['audio_descriptors'][0]['offset'], 133)
        self.assertTrue(r['audio_descriptors'][1]['mat_hash_not_required'])

    def test_duplicate_sads_retained(self):
        r = parse_edid(fixture(db(1, bytes.fromhex('677e03677e01'))))
        self.assertEqual(len(r['audio_descriptors']), 2)
        self.assertFalse(r['audio_descriptors'][1]['mat_hash_not_required'])

    def test_bad_checksum_yields_unknown(self):
        data = bytearray(fixture(db(1, bytes.fromhex('677e03'))))
        data[-1] ^= 1
        r = parse_edid(data)
        self.assertFalse(r['structure_ok'])
        self.assertIsNone(r['advertised']['mat_object_audio_pcm'])

    def test_truncated_base_and_extension(self):
        for data in (b'', b'\0'*127, fixture()[:-1]):
            self.assertFalse(parse_edid(data)['structure_ok'])

    def test_block_cannot_cross_dtd(self):
        self.assertFalse(parse_edid(fixture(bytes.fromhex('23677e')))['structure_ok'])

    def test_partial_sad(self):
        self.assertFalse(parse_edid(fixture(db(1, b'\x67\x7e')))['structure_ok'])

    def test_unknown_extension_preserved(self):
        extension = bytearray(128); extension[0] = 0x70
        r = parse_edid(fixture(extra=checksum(extension)))
        self.assertTrue(r['structure_ok'])
        self.assertEqual(r['blocks'][2]['tag'], 0x70)
        self.assertTrue(r['warnings'])

    def test_dolby_vision_is_not_audio(self):
        r = parse_edid(fixture(db(7, bytes.fromhex('0146d0004000'))))
        self.assertEqual(r['dolby_audio_blocks'], [])
        self.assertFalse(r['advertised']['mat_object_audio_pcm'])

    def test_dolby_audio_48k_exception(self):
        r = parse_edid(fixture(db(7, bytes.fromhex('1146d0006001'))))
        d = r['dolby_audio_blocks'][0]
        self.assertTrue(d['height_zone'])
        self.assertTrue(d['mat_pcm_48khz_only_no_truehd'])

    def test_dts_x_stays_a_hint(self):
        r = parse_edid(fixture(db(1, bytes.fromhex('5f7e02'))))
        self.assertTrue(r['advertised']['dts_x_reverse_engineered_hint'])
        self.assertNotIn('dts_x', r['advertised'])

    def test_extended_format_is_not_channel_count(self):
        r = parse_edid(fixture(db(1, bytes.fromhex('7f0758'))))
        self.assertEqual(r['audio_descriptors'][0]['format'], 'MPEG-H 3D Audio')
        self.assertNotIn('max_channels', r['audio_descriptors'][0])

    def test_zero_dtd_offset_has_no_collection(self):
        data = bytearray(fixture(db(1, bytes.fromhex('677e03'))))
        data[130] = 0
        data[128:] = checksum(data[128:])
        self.assertEqual(parse_edid(data)['audio_descriptors'], [])


if __name__ == '__main__':
    unittest.main()

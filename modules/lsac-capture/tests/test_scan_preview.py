import unittest
import xml.etree.ElementTree as ET

from lsac_capture.scan_preview import axis_domains,color_for_db,read_points,render_svg


def analysis():
    return dict(mode="arrival-preview",audio_time_basis="nominal-sampleclock",
                selection=dict(scope="selected_playback_interval",selected_host_duration_seconds=1.),
                relative_level_reference_scope="selected_playback_interval",channel_labels=["USB_ch1"],
                window_count=3,window_frames=4800,sample_rate=48000,
                windows=[dict(accepted=True,coordinate_frame="screen",acoustic_center_position_m=[-.1,.2,.3],
                              band_db_re_selected_max=[-12.],start_sample=0),
                         dict(accepted=True,coordinate_frame="screen",acoustic_center_position_m=[.1,-.2,.4],
                              band_db_re_selected_max=[0.],start_sample=4800),
                         dict(accepted=False,rejection="clipped")])


class ScanPreviewTests(unittest.TestCase):
    def test_svg_uses_selected_channel_and_explicit_preview_limitations(self):
        svg=render_svg(analysis(),source_name="example<&>.json")
        ET.fromstring(svg)
        self.assertIn("2 accepted / 3 windows",svg)
        self.assertIn("USB_ch1 relative to its selected-playback maximum",svg)
        self.assertIn("nominal-sampleclock",svg)
        self.assertIn("synchronization is UNVALIDATED",svg)
        self.assertIn("NOT dB SPL",svg)
        self.assertIn("NOMINAL 14-inch 16:10",svg)
        self.assertIn("example&lt;&amp;&gt;.json",svg)

    def test_projection_domains_share_cm_scale_and_include_screen_origin(self):
        _,points=read_points(analysis())
        domains=axis_domains(points)
        self.assertEqual(len({high-low for low,high in domains}),1)
        for low,high in domains:self.assertTrue(low<=0<=high)
        self.assertEqual(points[0]["cm"],[-10.,20.,30.])

    def test_rejects_ambiguous_reference_or_non_screen_positions(self):
        data=analysis();data["relative_level_reference_scope"]="whole_recording"
        with self.assertRaises(ValueError):read_points(data)
        data=analysis();data["windows"][0]["coordinate_frame"]="world"
        with self.assertRaises(ValueError):read_points(data)
        data=analysis();data["windows"][0]["band_db_re_selected_max"]=[float("nan")]
        with self.assertRaises(ValueError):read_points(data)

    def test_colorbar_clamps_without_changing_levels(self):
        self.assertEqual(color_for_db(-100),color_for_db(-30))
        self.assertEqual(color_for_db(2),color_for_db(0))
        self.assertNotEqual(color_for_db(-20),color_for_db(-10))


if __name__=="__main__":unittest.main()

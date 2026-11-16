"""Run from demo/: ../.venv-demo/Scripts/python.exe -m unittest test_short_format"""
import unittest
from unittest import mock

import prepare, reup, tiktok_post


class CaptionPieces(unittest.TestCase):
    def test_uses_claudes_pieces(self):
        with mock.patch.object(reup, 'ask_claude', return_value={'pieces': [['một hai', 'ba bốn.']]}):
            self.assertEqual(prepare.caption_pieces(['một hai ba bốn.'], '.'), [['một hai', 'ba bốn.']])

    def test_a_failed_answer_leaves_the_plain_cuts(self):
        with mock.patch.object(reup, 'ask_claude', side_effect=RuntimeError('offline')):
            self.assertIsNone(prepare.caption_pieces(['một hai ba bốn.'], '.'))


class CaptionWording(unittest.TestCase):
    EDITION = {'stories': [{'title': ['Tin'], 'detail': [], 'number': '17', 'unit': '', 'note': '1,5 triệu đồng'}]}

    def test_no_price_next_to_a_barred_service(self):
        wrong = tiktok_post.caption_problems('Công an phá đường dây mại dâm, giá 1,5 triệu đồng.', self.EDITION)
        self.assertTrue(any('số tiền' in w for w in wrong))

    def test_deaths_are_written_soberly(self):
        wrong = tiktok_post.caption_problems('Vụ nổ khiến 17 người chết.', self.EDITION)
        self.assertTrue(any('thiệt mạng' in w for w in wrong))
        self.assertEqual(tiktok_post.caption_problems('Vụ nổ khiến 17 người thiệt mạng.', self.EDITION), [])


class Pauses(unittest.TestCase):
    def test_silence_closing_the_clip_does_not_pin_a_piece(self):
        pieces = [['Một hai ba,'.split(), 'bốn năm sáu,'.split(), 'bảy tám chín mười.'.split()]]
        sentence = 'Một hai ba, bốn năm sáu, bảy tám chín mười.'
        given = [[' '.join(p) for p in pieces[0]]]
        with mock.patch.object(reup, 'inner_pauses', return_value=[1.0, 3.2]):
            cues = reup.cues([sentence], [(0, 3.4)], ['clip.wav'], given)
        self.assertAlmostEqual(cues[0][1], 1.0)  # the pause after the first comma pins it
        self.assertAlmostEqual(cues[1][1], 1.0 + 2.4 * 3 / 7)  # by word count from there, not at the end of the clip

    def test_a_pause_far_from_its_mark_pins_nothing(self):
        sentence = 'Một hai ba bốn năm sáu bảy tám, chín mười.'  # the voice paused after "hai", not at the comma
        with mock.patch.object(reup, 'inner_pauses', return_value=[.8]):
            cues = reup.cues([sentence], [(0, 4.0)], ['clip.wav'], [['Một hai ba bốn', 'năm sáu bảy tám,', 'chín mười.']])
        self.assertAlmostEqual(cues[1][1], 4.0 * .8)


if __name__ == '__main__':
    unittest.main()

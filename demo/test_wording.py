"""Run from demo/: ../.venv-demo/Scripts/python.exe -m unittest test_wording"""
import unittest

import reup, tiktok_post, wording


class Money(unittest.TestCase):
    def test_price_of_a_barred_service(self):
        self.assertTrue(wording.money_near_barred('Vé massage kèm bán dâm giá 1,5 triệu đồng mỗi vé.'))
        self.assertTrue(wording.money_near_barred('Đường dây đánh bạc trị giá hai trăm tỷ đồng.'))

    def test_only_within_one_sentence(self):
        self.assertEqual(wording.money_near_barred('Công an bắt đường dây ma túy. Giá vàng tăng hai trăm nghìn đồng.'), [])

    def test_accomplices_are_not_money(self):
        self.assertEqual(wording.money_near_barred('Khởi tố hai đồng phạm trong đường dây ma túy.'), [])

    def test_ordinary_business_news(self):
        self.assertEqual(wording.money_near_barred('Công ty lãi hai trăm tỷ đồng.'), [])


class Soften(unittest.TestCase):
    def test_keeps_the_case(self):
        self.assertEqual(wording.soften('17 người chết'), '17 người thiệt mạng')
        self.assertEqual(wording.soften('NGƯỜI CHẾT'), 'NGƯỜI THIỆT MẠNG')

    def test_leaves_other_text(self):
        self.assertEqual(wording.soften('Giá vàng tăng'), 'Giá vàng tăng')


class Masking(unittest.TestCase):
    def test_on_screen(self):
        self.assertEqual(wording.mask('Phá đường dây mại dâm'), 'Phá đường dây mại d.âm')
        self.assertEqual(wording.mask('ĐƯỜNG DÂY MA TÚY'), 'ĐƯỜNG DÂY MA T.ÚY')
        self.assertEqual(wording.mask('thu giữ súng, vũ khí'), 'thu giữ s.úng, v.ũ khí')

    def test_other_words_stay(self):
        self.assertEqual(wording.mask('Sung sức, vũ trụ, cá độc'), 'Sung sức, vũ trụ, cá độc')

    def test_voice_keeps_the_word_count(self):
        text = 'Tổ chức mại dâm và ma túy.'
        self.assertEqual(wording.to_voice(text), 'Tổ chức mãi dam và ma tuy.')
        self.assertEqual(len(wording.to_voice(text).split()), len(text.split()))


class Fallback(unittest.TestCase):
    def test_plain_caption_is_softened(self):
        edition = {'title': 'ĐIỂM TIN', 'programme': 'quoc-te', 'stories': [{'title': ['Nổ nhà máy', '17 người chết'], 'source': 'BBC • 10/11/2026'}]}
        self.assertIn('17 người thiệt mạng', tiktok_post.news_caption(edition))


class ReupScript(unittest.TestCase):
    def test_goes_back_to_claude(self):
        script = {'title': ['TIÊU ĐỀ'], 'sentences': ['Vụ nổ làm chết nhiều người.'], 'caption': ''}
        self.assertTrue(any('thiệt mạng' in p for p in reup.problems(script, 100)))

    def test_a_clean_script_passes(self):
        self.assertEqual(reup.problems({'title': ['TIÊU ĐỀ'], 'sentences': ['Vụ nổ làm nhiều người thiệt mạng.'], 'caption': ''}, 100), [])


if __name__ == '__main__':
    unittest.main()

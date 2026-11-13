import json, os, tempfile, unittest
from pathlib import Path
from unittest import mock

import tiktok_post
from tiktok_post import caption_problems, news_caption, write_caption

EDITION = {'date': '2026-11-07', 'programme': 'phap-luat', 'title': 'ĐIỂM TIN PHÁP LUẬT', 'stories': [
    {'title': ['NỮ ĐẠI GIA XĂNG DẦU', 'BỊ TUYÊN 18 NĂM TÙ'], 'detail': ['Tham ô 79 tỷ đồng'], 'number': '18', 'unit': 'NĂM TÙ', 'note': 'Án sơ thẩm', 'source': 'VnExpress • 05/11/2026', 'script': 's'},
    {'title': ['TỶ GIÁ USD GIẢM'], 'source': 'Thanh Niên • 05/11/2026', 'script': 't'}]}
GOOD = 'Nữ đại gia xăng dầu vừa bị tuyên 18 năm tù trong án sơ thẩm vì tham ô 79 tỷ đồng.'


class ProblemsTest(unittest.TestCase):
    def test_a_natural_sentence_passes(self):
        self.assertEqual(caption_problems(GOOD, EDITION), [])

    def test_template_words_are_refused(self):
        self.assertTrue(caption_problems('Điểm tin pháp luật hôm nay.', EDITION))

    def test_emoji_is_refused(self):
        self.assertTrue(caption_problems('Tỷ giá USD giảm 📌', EDITION))

    def test_questions_are_refused(self):
        self.assertTrue(caption_problems('Bản án này có nặng không?', EDITION))

    def test_all_capitals_are_refused(self):
        self.assertTrue(caption_problems('NỮ ĐẠI GIA XĂNG DẦU BỊ TUYÊN TÙ', EDITION))

    def test_a_lowercase_start_is_refused(self):
        self.assertTrue(caption_problems('tỷ giá USD giảm mạnh.', EDITION))

    def test_a_first_instance_verdict_must_say_so(self):
        wrong = caption_problems('Nữ đại gia xăng dầu bị tuyên 18 năm tù.', EDITION)
        self.assertTrue(any('sơ thẩm' in w for w in wrong))

    def test_known_abbreviations_are_fine(self):
        self.assertEqual(caption_problems('Tỷ giá USD giảm mạnh.', EDITION), [])

    def test_a_number_not_in_the_news_is_refused(self):
        wrong = caption_problems('Nữ đại gia bị tuyên 20 năm tù.', EDITION)
        self.assertTrue(any('20' in w for w in wrong))

    def test_too_long_is_refused(self):
        self.assertTrue(caption_problems('tin ' * 60, EDITION))

    def test_lines_and_hashtags_in_the_text_are_refused(self):
        self.assertTrue(caption_problems('Tỷ giá giảm\nmạnh', EDITION))
        self.assertTrue(caption_problems('Tỷ giá giảm #tintuc', EDITION))


class WriteTest(unittest.TestCase):
    def test_keeps_sources_and_adds_clean_tags(self):
        with mock.patch('reup.ask_claude', return_value={'text': GOOD, 'tags': ['#Xăng-dầu', 'phapluat', 'tintuc', 'thua']}):
            text = write_caption(EDITION, Path('.'))
        self.assertEqual(text, f'{GOOD}\n\nNguồn: VnExpress, Thanh Niên\n\n#tintuc #xngdu #phapluat')

    def test_a_bad_answer_goes_back_with_what_is_wrong(self):
        answers = [{'text': 'Điểm tin hôm nay', 'tags': []}, {'text': GOOD, 'tags': ['a']}]
        with mock.patch('reup.ask_claude', side_effect=answers) as ask:
            self.assertIn(GOOD, write_caption(EDITION, Path('.')))
        self.assertIn('điểm tin', ask.call_args_list[1].args[0].split('Bản trước')[1])

    def test_two_bad_answers_give_nothing(self):
        with mock.patch('reup.ask_claude', return_value={'text': 'Điểm tin', 'tags': []}):
            self.assertIsNone(write_caption(EDITION, Path('.')))

    def test_plain_caption_has_no_template_and_keeps_the_credit(self):
        text = news_caption(EDITION)
        self.assertEqual(text, 'NỮ ĐẠI GIA XĂNG DẦU BỊ TUYÊN 18 NĂM TÙ và 1 tin khác\n\nNguồn: VnExpress, Thanh Niên\n\n#tintuc #phapluat')


class PrepareTest(unittest.TestCase):
    DAY = '2026-11-07'

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.work, self.daily = root / 'work', root / 'daily'
        (self.work / self.DAY / 'phap-luat').mkdir(parents=True)
        (self.daily / self.DAY).mkdir(parents=True)
        (self.work / self.DAY / 'plan.json').write_text(json.dumps({'programmes': [{'id': 'phap-luat', 'status': 'ready'}]}), encoding='utf8')
        news = self.work / self.DAY / 'phap-luat' / 'news.json'
        news.write_text(json.dumps(EDITION), encoding='utf8')
        video = self.daily / self.DAY / 'phap-luat.mp4'
        video.write_bytes(b'x')
        stamp = news.stat().st_mtime + 1
        os.utime(video, (stamp, stamp))
        for name, value in (('WORK', self.work), ('DAILY', self.daily), ('no_caption', set())):
            patcher = mock.patch.object(tiktok_post, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def caption(self):
        return tiktok_post.news_videos(self.DAY)[0]['caption']

    def test_plain_until_written_then_written_once(self):
        self.assertTrue(self.caption().startswith('NỮ ĐẠI GIA'))
        with mock.patch('reup.ask_claude', return_value={'text': GOOD, 'tags': ['a']}) as ask:
            tiktok_post.prepare_caption(self.DAY, 'phap-luat')
            tiktok_post.prepare_caption(self.DAY, 'phap-luat')
        ask.assert_called_once()
        self.assertTrue(self.caption().startswith(GOOD))

    def test_claude_failing_leaves_the_plain_caption_and_is_not_asked_again(self):
        with mock.patch('reup.ask_claude', side_effect=RuntimeError('down')) as ask, mock.patch('builtins.print'):
            tiktok_post.prepare_caption(self.DAY, 'phap-luat')
            tiktok_post.prepare_caption(self.DAY, 'phap-luat')
        ask.assert_called_once()
        self.assertTrue(self.caption().startswith('NỮ ĐẠI GIA'))

    def test_changed_stories_get_a_new_caption(self):
        with mock.patch('reup.ask_claude', return_value={'text': GOOD, 'tags': ['a']}):
            tiktok_post.prepare_caption(self.DAY, 'phap-luat')
        news = self.work / self.DAY / 'phap-luat' / 'news.json'
        news.write_text(json.dumps({**EDITION, 'stories': EDITION['stories'][1:]}), encoding='utf8')
        video = self.daily / self.DAY / 'phap-luat.mp4'
        stamp = news.stat().st_mtime + 1
        os.utime(video, (stamp, stamp))
        self.assertTrue(self.caption().startswith('TỶ GIÁ USD'))


if __name__ == '__main__':
    unittest.main()

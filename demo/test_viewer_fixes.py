"""Run from demo/: ../.venv-demo/Scripts/python.exe -m unittest test_viewer_fixes"""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import prepare, reup, run_daily, sheet_to_news, tiktok_post, wording

STORY = {'title': ['Lính Mỹ bị bắt ở Anh', 'nghi làm gián điệp cho Nga'], 'number': '', 'unit': '', 'source': 'BBC • 11/11/2026',
         'detail': ['Bị bắt gần Gloucester, Anh', 'Cảnh sát nói điều tra đang tiếp diễn']}


class Numerals(unittest.TestCase):
    def test_numbers_become_digits(self):
        for words, digits in [('bốn mươi mốt tuổi', '41 tuổi'), ('lên một nghìn bảy trăm bốn mươi ba điểm.', 'lên 1.743 điểm.'),
                              ('tăng hai mươi tám phẩy bảy mươi lăm điểm,', 'tăng 28,75 điểm,'), ('giảm hai phần trăm', 'giảm 2%'),
                              ('ngày mười tháng mười một', 'ngày 10 tháng 11'), ('hơn một phẩy bốn tỷ đồng', 'hơn 1,4 tỷ đồng'),
                              ('năm hai nghìn không trăm hai mươi sáu', 'năm 2026'), ('hai mươi năm tù', '20 năm tù')]:
            self.assertEqual(wording.numerals(words), digits)

    def test_a_lone_digit_word_stays(self):
        for words in ['một quân nhân Mỹ', 'sau bốn năm tù', 'bảy đến tám năm tù', 'thứ Ba tuần này', 'mười mấy người']:
            self.assertEqual(wording.numerals(words), words)

    def test_a_number_read_two_ways_stays(self):
        self.assertEqual(wording.numerals('một trăm sáu nghìn tỷ đồng'), 'một trăm sáu nghìn tỷ đồng')

    def test_a_number_cut_in_two_captions_stays_in_words(self):
        said = [('Lên một nghìn bảy trăm bốn mươi ba điểm.', 0, 4, None)]
        pieces = [(0, 2, ['Lên', 'một', 'nghìn', 'bảy', 'trăm'], 0), (2, 4, ['bốn', 'mươi', 'ba', 'điểm.'], 0)]
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(reup, 'cues', return_value=pieces), \
                mock.patch.object(prepare, 'caption_pieces', return_value=None):
            prepare.write_captions(said, Path(folder) / 'captions.ass')
            written = (Path(folder) / 'captions.ass').read_text(encoding='utf8')
        self.assertIn('Lên một nghìn bảy trăm', written)
        self.assertNotIn('43', written)


class Figure(unittest.TestCase):
    def test_an_age_is_not_the_storys_number(self):
        self.assertEqual(sheet_to_news.figure_of('41', 'tuổi, tuổi của quân nhân bị bắt'), ('', ''))
        self.assertEqual(sheet_to_news.figure_of('73', 'Tuổi, cựu thủ tướng'), ('', ''))

    def test_a_unit_written_twice_is_said_once(self):
        self.assertEqual(sheet_to_news.figure_of('123', 'triệu lít, triệu lít xăng dầu âm'), ('123', 'triệu lít xăng dầu âm'))

    def test_a_version_number_is_not_a_figure(self):
        self.assertEqual(sheet_to_news.figure_of('27.2.1', 'bản cập nhật bảo mật'), ('', ''))
        self.assertEqual(sheet_to_news.figure_of('25.760', 'đồng/USD'), ('25.760', 'đồng/USD'))
        self.assertEqual(sheet_to_news.figure_of('1.200.000', 'tấn'), ('1.200.000', 'tấn'))

    def test_a_real_figure_stays(self):
        self.assertEqual(sheet_to_news.figure_of('7-8', 'năm tù đề nghị'), ('7-8', 'năm tù đề nghị'))

    def test_every_layout_draws_a_story_without_a_figure(self):
        for layout in ['goc', 'anh-tran-tren', 'anh-tran-vien', 'the-kem']:
            built = prepare.card({}, STORY, 0, layout)
            self.assertIn('Gloucester', built['full'], layout)
            self.assertNotIn('<text x="84" y="NaN"', built['full'])


class Attribution(unittest.TestCase):
    def test_the_outlet_is_not_read_out(self):
        script = 'Dương Đông âm kho. Theo VnExpress, thanh tra kết luận có dấu hiệu vi phạm.'
        self.assertEqual(sheet_to_news.unattributed(script, 'VnExpress • 10/11/2026'), 'Dương Đông âm kho. Thanh tra kết luận có dấu hiệu vi phạm.')

    def test_an_outlet_quoting_another(self):
        script = 'iPhone nên cập nhật. Theo Thanh Niên, dẫn AppleInsider, bản vá sửa lỗi.'
        self.assertEqual(sheet_to_news.unattributed(script, 'Thanh Niên • 10/11'), 'iPhone nên cập nhật. Bản vá sửa lỗi.')

    def test_an_outlet_named_in_full(self):
        script = 'Mười hai người thiệt mạng. Theo Deutsche Welle, khoảng năm mươi xe dính vào vụ va chạm.'
        self.assertEqual(sheet_to_news.unattributed(script, 'DW • 10/11/2026'), 'Mười hai người thiệt mạng. Khoảng năm mươi xe dính vào vụ va chạm.')

    def test_who_said_it_stays(self):
        script = 'Hải quan xử lý nhiều vụ. Theo Cục Hải quan, trị giá hàng hóa rất lớn.'
        self.assertEqual(sheet_to_news.unattributed(script, 'CafeF • 10/11'), script)


class Steps(unittest.TestCase):
    def test_detail_lines_are_layers_of_their_own(self):
        built = prepare.card({}, STORY, 0, 'anh-tran-vien')
        self.assertEqual(len(built['steps']), 2)
        self.assertIn('Gloucester', built['steps'][0])
        self.assertNotIn('Gloucester', built['head'])
        self.assertNotIn('Gloucester', built['cover'])
        self.assertIn('Lính Mỹ', built['head'])
        self.assertIn('Gloucester', built['text'])  # the long videos still draw the whole card at once

    def test_they_come_in_with_the_later_sentences(self):
        self.assertEqual(prepare.step_times('Một hai ba bốn. Năm sáu. Bảy tám chín mười.', 10, 2), [4.0, 6.0])
        self.assertEqual(prepare.step_times('Một hai ba bốn năm. Sáu bảy tám chín mười.', 10, 2), [5.0, 7.5])


class KeepUnchangedVideos(unittest.TestCase):
    def test_only_the_changed_programme_is_made_again(self):
        made = {'voice': 'A', 'speed': 1.25, 'short': True}
        with tempfile.TemporaryDirectory() as folder:
            work, out = Path(folder) / 'work', Path(folder) / 'out'
            for pid in ('tong-hop', 'quoc-te'):
                (work / '2026-11-11' / pid).mkdir(parents=True)
            out.mkdir()
            (work / '2026-11-11' / 'plan.json').write_text(json.dumps({'programmes': [
                {'id': 'tong-hop', 'status': 'ready', 'unchanged': True}, {'id': 'quoc-te', 'status': 'ready'}]}))
            (work / '2026-11-11' / 'tong-hop' / 'done.json').write_text(json.dumps(made))
            (out / 'tong-hop.mp4').write_bytes(b'x')
            with mock.patch.object(run_daily, 'WORK', work), mock.patch.object(run_daily, 'day_output', return_value=out):
                self.assertEqual([p['id'] for p in run_daily.ready_programmes('2026-11-11', set(), made)], ['quoc-te'])
                other = {**made, 'speed': 1.0}  # another reading speed: the old video no longer stands
                self.assertEqual(len(run_daily.ready_programmes('2026-11-11', set(), other)), 2)
                self.assertEqual(len(run_daily.ready_programmes('2026-11-11', {'tong-hop'})), 1)  # asked for by name

    def test_the_sheet_step_keeps_the_folder_of_an_unchanged_edition(self):
        edition = {'date': '2026-11-11', 'programme': 'a', 'title': 'A', 'stories': [{'title': ['x']}]}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'news.json'
            sheet_to_news.save_edition(edition, path)
            self.assertTrue(sheet_to_news.same_edition(path, edition))
            self.assertFalse(sheet_to_news.same_edition(path, {**edition, 'stories': [{'title': ['y']}]}))
            self.assertFalse(sheet_to_news.same_edition(Path(folder) / 'missing.json', edition))


class PostingOrder(unittest.TestCase):
    def test_videos_repeating_the_round_up_go_last(self):
        def video(pid, *titles):
            return {'id': pid, 'edition': {'stories': [{'title': [t]} for t in titles]}}
        videos = [video('tong-hop', 'a', 'b', 'c'), video('phap-luat', 'a', 'b'), video('quoc-te', 'x'), video('tai-chinh', 'c', 'y')]
        for _ in range(10):
            self.assertEqual(tiktok_post.build_order({}, videos), ['tong-hop', 'quoc-te', 'tai-chinh', 'phap-luat'])


class HoldTheViewer(unittest.TestCase):
    def test_progress_bar_marks_the_stories_told(self):
        built = prepare.card({}, STORY, 1, 'goc', progress=(1, 4))
        self.assertEqual(built['head'].count('height="8"'), 4)
        self.assertEqual(built['head'].count('fill-opacity="0.38"'), 2)
        self.assertNotIn('height="8"', prepare.card({}, STORY, 1, 'goc')['head'])

    def test_a_headline_does_not_end_on_one_word(self):
        lines, _ = prepare.headline({'title': ['Quốc hội Israel khóa mới', 'tuyên thệ nhậm chức']}, 68, 796)
        self.assertGreater(len(lines[-1].split()), 1)

    def test_a_word_alone_joins_the_next_caption(self):
        pieces = reup.pieces_of('có ghế, khối ông Benjamin Netanyahu', ['có ghế,', 'khối', 'ông Benjamin Netanyahu'])
        self.assertNotIn(['khối'], pieces)
        self.assertEqual([w for p in pieces for w in p], 'có ghế, khối ông Benjamin Netanyahu'.split())


class AskClaude(unittest.TestCase):
    def test_asks_again_when_the_turns_ran_out(self):
        answers = [mock.Mock(stdout=json.dumps({'is_error': True, 'subtype': 'error_max_turns'}), stderr=''),
                   mock.Mock(stdout=json.dumps({'result': '{"text": "ok"}'}), stderr='')]
        with mock.patch.object(reup.subprocess, 'run', side_effect=answers) as run, mock.patch.object(reup, 'claude_bin', return_value='claude'):
            self.assertEqual(reup.ask_claude('Viết caption', '.'), {'text': 'ok'})
        self.assertEqual(run.call_count, 2)
        self.assertIn('Trả lời ngay', run.call_args.kwargs['input'])


if __name__ == '__main__':
    unittest.main()

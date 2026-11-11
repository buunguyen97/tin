import json, tempfile, unittest
from pathlib import Path
from unittest import mock

import ai_image
import fetch_images
from fetch_images import stock_photo


class StockPhotoTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.stock, self.assets = tmp / 'stock', tmp / 'assets'
        self.assets.mkdir()
        for sector, n in [('phap_luat', 3), ('vi_mo_chinh_sach', 1), ('vang', 1)]:
            (self.stock / sector).mkdir(parents=True)
            for k in range(1, n + 1):
                (self.stock / sector / f'{k:02d}.jpg').write_bytes(b'jpg')
        (self.stock / 'credits.json').write_text(json.dumps({'phap_luat/01.jpg': {'license': 'Public domain'}}))
        patches = [mock.patch.object(fetch_images, 'stock', self.stock), mock.patch.object(fetch_images, 'assets', self.assets)]
        for p in patches:
            p.start(); self.addCleanup(p.stop)

    def story(self, k, sector='phap_luat', title=('Tin',)):
        return {'url': f'https://example.vn/tin-{k}', 'sector': sector, 'title': list(title)}

    def test_stories_of_a_video_get_different_photos(self):
        used = set()
        names = [stock_photo(self.story(k), used)[0] for k in range(3)]
        self.assertEqual(len(set(names)), 3)
        self.assertTrue(all(n.startswith('img-stock-phap_luat-') and (self.assets / n).exists() for n in names))

    def test_same_story_keeps_its_photo(self):
        self.assertEqual(stock_photo(self.story(7), set()), stock_photo(self.story(7), set()))

    def test_credit_follows_the_licence(self):
        credits = {stock_photo(self.story(k), set())[1] for k in range(20)}
        self.assertEqual(credits, {'Wikimedia Commons (Public domain)', 'Wikimedia Commons (CC0)'})

    def test_unknown_sector_uses_the_default(self):
        self.assertEqual(stock_photo(self.story(1, 'khac'), set())[0], 'img-stock-vi_mo_chinh_sach-01.jpg')

    def test_topic_photo_before_sector_photo(self):
        used = set()
        gold = self.story(1, 'tai_chinh_ngan_hang', ['Giá vàng', 'tăng mạnh'])
        self.assertEqual(stock_photo(gold, used)[0], 'img-stock-vang-01.jpg')
        # The only gold photo is taken: the next gold story gets one of its sector.
        self.assertTrue(stock_photo(self.story(2, 'phap_luat', ['Vàng', 'giả']), used)[0].startswith('img-stock-phap_luat-'))

    def test_more_stories_than_photos_still_get_one(self):
        used = set()
        self.assertTrue(all(stock_photo(self.story(k), used) for k in range(5)))


class DrawnPictureTest(unittest.TestCase):
    def setUp(self):
        self.assets = Path(tempfile.mkdtemp())
        p = mock.patch.object(fetch_images, 'assets', self.assets)
        p.start(); self.addCleanup(p.stop)
        self.story = {'url': 'https://example.vn/tin', 'title': ['Tin']}

    def test_drawn_once_then_cached(self):
        def draw(story, out):
            Path(out).write_bytes(b'png')
        with mock.patch.object(fetch_images.ai_image, 'draw', side_effect=draw) as drawing:
            first = fetch_images.drawn_picture(self.story)
            self.assertEqual(fetch_images.drawn_picture(self.story), first)
        self.assertEqual(drawing.call_count, 1)
        self.assertEqual(first[1], 'Đồ họa AI')


class CheckSvgTest(unittest.TestCase):
    GOOD = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 9 9"><defs><linearGradient id="a"/></defs><rect fill="url(#a)"/></svg>'

    def test_svg_is_cut_out_of_the_answer(self):
        self.assertEqual(ai_image.check_svg('Đây:\n' + self.GOOD + '\nxong'), self.GOOD)

    def test_no_svg_or_unsafe_svg_is_refused(self):
        for answer in ['Xin lỗi', '<svg><script>x()</script></svg>', '<svg><image href="http://x/y.png"/></svg>',
                       '<svg><rect onload="x()"/></svg>', '<svg><rect fill="url(http://x/y)"/></svg>',
                       '<svg><foreignObject/></svg>', '<svg><a href="http://x"/></svg>']:
            with self.assertRaises(ValueError, msg=answer):
                ai_image.check_svg(answer)


def _page(index, title, licence, width=2000, height=1300):
    return {'index': index, 'title': f'File:{title}.jpg', 'imageinfo': [{
        'width': width, 'height': height, 'url': f'https://upload.example/{index}.jpg', 'thumburl': f'https://thumb.example/{index}.jpg',
        'extmetadata': {'LicenseShortName': {'value': licence}}}]}


class CommonsCandidatesTest(unittest.TestCase):
    def test_only_free_landscape_modern_photos_in_search_order(self):
        pages = [_page(1, 'Knesset at night', 'CC BY-SA 4.0'),      # needs attribution and share-alike
                 _page(2, 'Knesset tower', 'CC0', 1000, 1600),      # portrait
                 _page(3, 'Knesset panorama', 'CC0', 8000, 1000),   # too wide for any card
                 _page(4, 'Knesset stereo card', 'Public domain'),  # an old scan
                 _page(5, 'Knesset small', 'CC0', 800, 600),
                 _page(7, 'Knesset garden', 'CC0'),
                 _page(6, 'Knesset from the south', 'Public domain')]
        answer = json.dumps({'query': {'pages': {str(p['index']): p for p in pages}}}).encode()
        with mock.patch.object(fetch_images.subprocess, 'run', return_value=mock.Mock(returncode=0, stdout=answer)):
            found = fetch_images.commons_candidates('Knesset')
        self.assertEqual([f['title'] for f in found], ['Knesset from the south.jpg', 'Knesset garden.jpg'])
        self.assertEqual(found[0]['licence'], 'Public domain')

    def test_commons_busy_gives_nothing(self):
        with mock.patch.object(fetch_images.subprocess, 'run', return_value=mock.Mock(returncode=0, stdout=b'<html>busy')):
            self.assertEqual(fetch_images.commons_candidates('Knesset'), [])


class FoundPhotoTest(unittest.TestCase):
    def setUp(self):
        self.assets = Path(tempfile.mkdtemp())
        self.story = {'url': 'https://example.vn/tin', 'title': ['Tin'], 'image_query': 'Knesset'}
        def fake_download(url, out):
            Path(out).write_bytes(b'jpg')
            return True
        patches = [mock.patch.object(fetch_images, 'assets', self.assets),
                   mock.patch.object(fetch_images, 'download_photo', fake_download),
                   mock.patch.object(fetch_images.time, 'sleep'),
                   mock.patch.object(fetch_images, 'commons_candidates',
                                     return_value=[{'title': 'A.jpg', 'licence': 'CC0', 'url': 'u1'}, {'title': 'B.jpg', 'licence': 'Public domain', 'url': 'u2'}]),
                   mock.patch.object(fetch_images.ai_image, 'search_terms', return_value=['Knesset'])]
        for p in patches:
            p.start(); self.addCleanup(p.stop)

    def test_claude_choice_is_kept_with_its_licence_and_not_asked_again(self):
        with mock.patch.object(fetch_images.ai_image, 'pick_photo', return_value='02.jpg') as pick:
            name, credit = fetch_images.found_photo(self.story)
            self.assertEqual(fetch_images.found_photo(self.story), (name, credit))
        self.assertEqual(credit, 'Wikimedia Commons (Public domain)')
        self.assertTrue((self.assets / name).exists())
        self.assertEqual(pick.call_count, 1)
        self.assertEqual(pick.call_args.args[2], {'01.jpg': 'A.jpg', '02.jpg': 'B.jpg'})

    def test_no_fitting_photo_is_remembered(self):
        with mock.patch.object(fetch_images.ai_image, 'pick_photo', return_value=None) as pick:
            self.assertIsNone(fetch_images.found_photo(self.story))
            self.assertIsNone(fetch_images.found_photo(self.story))
        self.assertEqual(pick.call_count, 1)

    def test_commons_down_is_not_remembered(self):
        with mock.patch.object(fetch_images, 'commons_candidates', return_value=[]):
            self.assertIsNone(fetch_images.found_photo(self.story))
        with mock.patch.object(fetch_images.ai_image, 'pick_photo', return_value='01.jpg'):
            self.assertIsNotNone(fetch_images.found_photo(self.story))


class ClaudeAnswersTest(unittest.TestCase):
    def test_search_terms_start_with_the_editor_keyword(self):
        with mock.patch.object(ai_image, 'ask_claude', return_value='["Knesset building", "Knesset", "Jerusalem"]'):
            self.assertEqual(ai_image.search_terms({'title': ['T']}, 'Knesset'), ['Knesset', 'Knesset building', 'Jerusalem'])

    def test_pick_refuses_a_file_it_was_not_shown(self):
        photos = {'01.jpg': 'A'}
        for answer, expected in [('{"file": "01.jpg"}', '01.jpg'), ('Chọn: {"file": null}', None), ('{"file": "../secret.jpg"}', None)]:
            with mock.patch.object(ai_image, 'ask_claude', return_value=answer):
                self.assertEqual(ai_image.pick_photo({'title': ['T']}, 'x', photos), expected)

    def test_an_answer_without_json_is_an_error(self):
        with mock.patch.object(ai_image, 'ask_claude', return_value='Xin lỗi'):
            with self.assertRaises(ValueError):
                ai_image.pick_photo({'title': ['T']}, 'x', {'01.jpg': 'A'})


if __name__ == '__main__':
    unittest.main()

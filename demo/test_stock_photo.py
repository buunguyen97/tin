import json, tempfile, unittest
from pathlib import Path
from unittest import mock

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


def _page(index, title, licence, width=2000, height=1300):
    return {'index': index, 'title': f'File:{title}.jpg', 'imageinfo': [{
        'width': width, 'height': height, 'url': f'https://upload.example/{index}.jpg', 'thumburl': f'https://thumb.example/{index}.jpg',
        'extmetadata': {'LicenseShortName': {'value': licence}}}]}


class CommonsPhotoTest(unittest.TestCase):
    PAGES = [_page(1, 'Knesset building at night', 'CC BY-SA 4.0'),  # needs attribution and share-alike
             _page(2, 'Protest in Tel Aviv', 'CC0'),                   # the search matched its description only
             _page(3, 'Knesset building tower', 'CC0', 1000, 1600),   # portrait
             _page(4, 'The Knesset Building from the south', 'Public domain'),
             _page(5, 'Knesset building 2', 'CC0')]

    def setUp(self):
        self.assets = Path(tempfile.mkdtemp())
        p = mock.patch.object(fetch_images, 'assets', self.assets)
        p.start(); self.addCleanup(p.stop)
        self.fetched = []

    def run_curl(self, cmd, capture_output):
        if '-o' in cmd:
            self.fetched.append(cmd[-1])
            Path(cmd[cmd.index('-o') + 1]).write_bytes(b'x' * 30000)
            return mock.Mock(returncode=0, stdout=b'')
        pages = {str(p['index']): p for p in self.PAGES}
        return mock.Mock(returncode=0, stdout=json.dumps({'query': {'pages': pages}}).encode())

    def test_first_free_landscape_photo_named_after_the_query(self):
        with mock.patch.object(fetch_images.subprocess, 'run', self.run_curl):
            name, credit = fetch_images.commons_photo('Knesset building')
            self.assertEqual((self.fetched, credit), (['https://thumb.example/4.jpg'], 'Wikimedia Commons (Public domain)'))
            self.assertEqual(fetch_images.commons_photo('knesset BUILDING'), (name, credit))  # cached
            self.assertEqual(len(self.fetched), 1)

    def test_nothing_free_gives_none(self):
        self.PAGES = [_page(1, 'Knesset building', 'CC BY 2.0')]
        with mock.patch.object(fetch_images.subprocess, 'run', self.run_curl):
            self.assertIsNone(fetch_images.commons_photo('Knesset building'))


if __name__ == '__main__':
    unittest.main()

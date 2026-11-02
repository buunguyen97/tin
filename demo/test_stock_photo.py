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
        for sector, n in [('phap_luat', 3), ('vi_mo_chinh_sach', 1)]:
            (self.stock / sector).mkdir(parents=True)
            for k in range(1, n + 1):
                (self.stock / sector / f'{k:02d}.jpg').write_bytes(b'jpg')
        (self.stock / 'credits.json').write_text(json.dumps({'phap_luat/01.jpg': {'license': 'Public domain'}}))
        patches = [mock.patch.object(fetch_images, 'stock', self.stock), mock.patch.object(fetch_images, 'assets', self.assets)]
        for p in patches:
            p.start(); self.addCleanup(p.stop)

    def story(self, k, sector='phap_luat'):
        return {'url': f'https://example.vn/tin-{k}', 'sector': sector}

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

    def test_more_stories_than_photos_still_get_one(self):
        used = set()
        self.assertTrue(all(stock_photo(self.story(k), used) for k in range(5)))


if __name__ == '__main__':
    unittest.main()

import unittest

from image_rights import judge, photo_credit, video_allowed


class JudgeTest(unittest.TestCase):
    def test_handed_out_photos_are_used(self):
        for credit in ['VGP', 'Bộ Công an', 'Công an TP HCM', 'Cổng thông tin điện tử Quốc hội', 'HSG', 'MSB',
                       'AEF 2026', 'VinEnergo', 'Vietravel Airlines', 'BQL vịnh Hạ Long - Yên Tử', 'Công an cung cấp',
                       'thainguyen.gov.vn', 'Unsplash', 'Wikimedia Commons (CC0)',
                       'Wikimedia Commons (public domain)', 'Obayashi Việt Nam', 'FPT', 'QH', 'PNJ',
                       'White House', 'Kremlin.ru', 'President.gov.ua', 'GOV.UK', 'European Commission', 'NASA']:
            self.assertTrue(judge(credit)[0], credit)

    def test_owned_photos_are_dropped(self):
        for credit in ['', 'Ngọc Thành', 'Trần Thế Bằng', 'HỮU HẠNH', 'HÙNG - TRIỀU', 'L an Hương', 'Reuters',
                       'AFP', 'TTXVN', 'AI', 'Báo Nhân dân', 'Nhân Dân / Tuyền Vy', 'VTV.vn', 'CTV', 'SĐ',
                       'N.H', 'Nguyễn Việt Nam', 'Reuters/White House', 'AP Photo']:
            self.assertFalse(judge(credit)[0], credit)


class VideoTest(unittest.TestCase):
    def test_only_free_footage(self):
        for credit in ['Wikimedia Commons (CC0)', 'Wikimedia Commons (public domain)', 'NASA']:
            self.assertTrue(video_allowed(credit)[0], credit)
        for credit in ['', 'Reuters', 'AP', 'BBC', 'CNN', 'Bộ Công an', 'VTV']:
            self.assertFalse(video_allowed(credit)[0], credit)


class PhotoCreditTest(unittest.TestCase):
    def test_caption_of_the_same_image(self):
        page = ('<img src="https://cdn.example/2026/10/30/gian-khoan-123.jpg" alt="x">'
                '<figcaption itemprop="description"><p class="Image">Giàn khoan dầu khí ngoài khơi. Ảnh: PVN</p></figcaption>')
        self.assertEqual(photo_credit(page, 'https://cdn.example/thumb/gian-khoan-123.jpg?w=1200'), 'PVN')

    def test_share_crop_falls_back_to_first_article_photo(self):
        page = ('<meta property="og:image" content="https://cdn.example/avatar999.jpg">'
                '<img itemprop="contentUrl" alt="Bảng hiệu ngân hàng SCB. Ảnh: Ngọc Thành" src="data:,">')
        self.assertEqual(photo_credit(page, 'https://cdn.example/avatar999.jpg'), 'Ngọc Thành')

    def test_tuoi_tre_dash_credit(self):
        page = ('<img src="https://cdn.example/artemis-1.jpg">'
                '<figcaption>Đại diện Việt Nam trao văn kiện - Ảnh: BTC</figcaption>')
        self.assertEqual(photo_credit(page, 'https://cdn.example/artemis-1.jpg'), 'BTC')

    def test_no_caption(self):
        self.assertEqual(photo_credit('<p>no photo</p>', 'https://cdn.example/a.jpg'), '')


if __name__ == '__main__':
    unittest.main()

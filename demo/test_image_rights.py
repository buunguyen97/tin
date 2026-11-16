import unittest

from image_rights import article_photos, attribution, handed_out, judge, photo_credit, photo_label, video_allowed


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


class SeveralPhotosTest(unittest.TestCase):
    def test_each_photo_takes_its_own_caption(self):
        page = ('<img src="https://cdn.vn/a/logo.png">'
                '<figure><img data-src="https://cdn.vn/2026/1.jpg"><figcaption>Hiện trường. Ảnh: Công an cung cấp</figcaption></figure>'
                '<p>Đoạn văn.</p><img src="https://cdn.vn/2026/2.jpg"><p>Không có chú thích.</p>'
                '<figure><img src="https://cdn.vn/2026/3.jpg"><figcaption>Ông A. Ảnh: Ngọc Thành</figcaption></figure>'
                '<img src="https://cdn.vn/2026/1.jpg">')
        self.assertEqual(article_photos(page), [('https://cdn.vn/2026/1.jpg', 'Công an cung cấp'), ('https://cdn.vn/2026/2.jpg', ''),
                                                ('https://cdn.vn/2026/3.jpg', 'Ngọc Thành')])

    def test_further_photos_need_a_plain_owner(self):
        for credit in ('Công an TP HCM', 'VGP/Nhật Bắc', 'Bộ Y tế', 'Obayashi Việt Nam', 'White House'):
            self.assertTrue(handed_out(credit), credit)
        for credit in ('TL', 'CA LINH', 'X/Egyosint', 'Ngọc Thành', '', 'Reuters'):
            self.assertFalse(handed_out(credit), credit)

    def test_labels_name_the_author_when_the_licence_asks(self):
        self.assertEqual(attribution('Wikimedia Commons (CC BY-SA 4.0, Anders Eidesvik)'), 'Anders Eidesvik, CC BY-SA 4.0')
        self.assertEqual(photo_label('Wikimedia Commons (CC BY 4.0, Ann)'), 'Ảnh minh họa · Ann, CC BY 4.0')
        self.assertEqual(photo_label('Wikimedia Commons (CC0)'), 'Ảnh minh họa')
        self.assertEqual(photo_label('Wikimedia Commons (CC BY-SA 2.0, Bob)', clip=True), 'Video minh họa · Bob, CC BY-SA 2.0')
        self.assertEqual(photo_label('Đồ họa AI'), 'Đồ họa AI')
        self.assertEqual(photo_label('Kremlin.ru'), 'Ảnh: Kremlin.ru')
        self.assertIsNone(photo_label('Bộ Công an'))


if __name__ == '__main__':
    unittest.main()

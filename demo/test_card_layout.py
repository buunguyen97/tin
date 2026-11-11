"""Run from demo/: ../.venv-demo/Scripts/python.exe -m unittest test_card_layout"""
import re, unittest

import prepare

SHORT = {'title': ['VIX thanh khoản đột biến'], 'number': '105', 'unit': 'triệu cổ phiếu', 'detail': ['Khối ngoại mua ròng'], 'source': 'CafeF'}
LONG = {'title': ['Đe dọa đánh bom giả nhắm', 'hàng loạt chuyến bay quốc tế'], 'number': '600.000', 'unit': 'người biểu tình theo Bộ Nội vụ Pháp',
        'detail': ['CGT: 1,57 triệu người tham gia', 'Pháp triển khai 70.000 cảnh sát'], 'source': 'BBC'}


def lowest_baseline(svg):
    """Where the source line, the last text on a card, ends once the card's shift is applied."""
    shift = int(re.search(r'translate\(0 (\d+)\)', svg).group(1))
    return shift + max(int(y) for y in re.findall(r'<text x="[^"]*" y="(\d+)"', svg))


class CardLayout(unittest.TestCase):
    def test_text_ends_low_in_the_safe_area_without_passing_it(self):
        for layout in prepare.LAYOUTS:
            for item in (SHORT, LONG):
                with self.subTest(layout=layout, title=item['title']):
                    built = prepare.card({}, item, 0, layout)
                    end = lowest_baseline(built['text'])
                    self.assertLessEqual(end, prepare.SAFE['bottom'])
                    if built['grow'] < prepare.GROW_MOST:  # only a very short story may still end higher
                        self.assertGreaterEqual(end, prepare.SAFE['bottom'] - 40)

    def test_photo_grows_by_the_space_the_text_moved(self):
        built = prepare.card({}, SHORT, 0, 'goc')
        self.assertGreater(built['grow'], 0)
        self.assertEqual(prepare.photo_area('goc', built['grow'])[3], prepare.WINDOWS['goc'][3] + built['grow'])

    def test_layers_are_separate(self):
        for layout in prepare.LAYOUTS:
            built = prepare.card({}, SHORT, 0, layout, stock=True)
            self.assertNotIn('<text', built['mask'])  # text must not sit in the still layer
            self.assertNotIn('font-face', built['mask'])
            self.assertIn('Ảnh minh họa', built['text'])
            self.assertIn('Ảnh minh họa', built['full'])

    def test_subtitle_card_darkens_behind_its_text(self):
        built = prepare.card({}, LONG, 0, 'phu-de')
        first = min(int(y) for y in re.findall(r'<text x="[^"]*" y="(\d+)"', built['text']))
        self.assertGreater(first - 60, sum(prepare.SCRIM) - 1)  # the whole headline is on the dark part
        self.assertNotIn('Barlow', built['full'])


if __name__ == '__main__':
    unittest.main()

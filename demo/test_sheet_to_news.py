import csv
import unittest
from datetime import date
from pathlib import Path
from edition import default_programmes, normalise, number_words, spoken_date
from sheet_to_news import SheetError, approved_stories, parse_date, plan_programmes, rows_to_dicts

TEMPLATE = Path(__file__).resolve().parent / 'sheet-template.csv'
DAY = date(2026, 10, 27)
COL = {normalise(h): i for i, h in enumerate(next(csv.reader(open(TEMPLATE, encoding='utf-8-sig'))))}


def template_rows():
    with open(TEMPLATE, encoding='utf-8-sig') as f:
        return list(csv.reader(f))


def plan(rows, programmes=None):
    stories, warnings, errors = approved_stories(rows_to_dicts(rows), DAY)
    plans = {p['id']: (e, reason) for p, e, reason in plan_programmes(stories, programmes or default_programmes(), DAY)}
    return plans, warnings, errors


class SheetTests(unittest.TestCase):
    def test_vietnamese_headers_are_normalised(self):
        self.assertEqual(normalise('Trạng thái'), 'trang_thai')
        self.assertEqual(normalise(' Đơn vị '), 'don_vi')
        self.assertEqual(normalise('Thứ tự hot'), 'thu_tu_hot')

    def test_dates_in_sheet_formats(self):
        for value in ['2026-10-27', '27/10/2026', '27/10', '27-10-2026']:
            self.assertEqual(parse_date(value, 2026), DAY, value)
        self.assertIsNone(parse_date('hôm nay', 2026))

    def test_template_plans_roundup_and_sectors_with_enough_stories(self):
        plans, warnings, errors = plan(template_rows())
        self.assertEqual((warnings, errors), ([], []))
        ready = {k for k, (e, _) in plans.items() if e}
        self.assertEqual(ready, {'tong-hop', 'vi-mo-chinh-sach', 'doanh-nghiep'})
        self.assertIn('1 tin', plans['hang-khong-du-lich'][1])  # below the minimum of 2
        roundup = plans['tong-hop'][0]
        self.assertEqual([s['number'] for s in roundup['stories']], ['230', '16', '129,3%', '25.10', '10'])
        self.assertEqual(roundup['intro'][:51], 'Điểm tin nổi bật ngày hai mươi bảy tháng mười. Nhữn')
        sector = plans['doanh-nghiep'][0]
        self.assertEqual([s['number'] for s in sector['stories']], ['230', '129,3%'])
        self.assertEqual({s['accent'] for s in sector['stories']}, {'#FFA9B6'})  # one colour per sector

    def test_hot_order_and_max(self):
        rows = template_rows()
        for i, r in enumerate(rows[1:], 1):
            r[COL['thu_tu_hot']] = str(10 - i)  # reverse
        rows[3][COL['hot']] = ''  # story 3 not hot
        programmes = default_programmes()
        programmes[0]['max'] = 3
        plans, _, _ = plan(rows, programmes)
        self.assertEqual([s['number'] for s in plans['tong-hop'][0]['stories']], ['10', '25.10', '16'])

    def test_only_approved_rows_for_the_day(self):
        rows = template_rows()
        rows[2][COL['trang_thai']] = 'Nháp'
        rows[5][COL['ngay']] = '26/10/2026'
        plans, _, _ = plan(rows)
        self.assertEqual([s['number'] for s in plans['tong-hop'][0]['stories']], ['230', '129,3%', '25.10'])
        self.assertIsNone(plans['vi-mo-chinh-sach'][0])

    def test_bad_row_is_skipped_not_fatal(self):
        rows = template_rows()
        rows[1][COL['kich_ban']] = ''
        plans, _, errors = plan(rows)
        self.assertEqual(errors, ['Dòng 2: thiếu kich_ban; bỏ qua tin này'])
        self.assertEqual(len(plans['tong-hop'][0]['stories']), 4)

    def test_overlong_title_and_digits_in_script_warn(self):
        rows = template_rows()
        rows[1][COL['tieu_de_1']] = 'Một tiêu đề quá dài để vừa một dòng'
        rows[1][COL['kich_ban']] = 'Lãi suất 8% một năm.'
        _, warnings, _ = plan(rows)
        self.assertTrue(any('tieu_de_1' in w for w in warnings))
        self.assertTrue(any('chữ số' in w for w in warnings))

    def test_old_story_is_dropped(self):
        rows = template_rows()
        rows[1][COL['thoi_gian_dang']] = '26/10/2026 16:59'  # before 17:00 the evening before
        rows[2][COL['thoi_gian_dang']] = '2026-10-26 17:00'  # exactly on the cutoff: kept
        plans, _, errors = plan(rows)
        self.assertEqual(len(errors), 1)
        self.assertIn('Dòng 2: đăng lúc 16:59 26/10', errors[0])
        self.assertEqual([s['number'] for s in plans['tong-hop'][0]['stories']], ['16', '129,3%', '25.10', '10'])

    def test_missing_publish_time_is_dropped(self):
        rows = template_rows()
        rows[3][COL['thoi_gian_dang']] = ''
        _, _, errors = plan(rows)
        self.assertIn('Dòng 4: thiếu hoặc sai "Thời gian đăng"', errors[0])

    def test_story_already_used_on_an_earlier_day_is_dropped(self):
        rows = template_rows()
        repeat = list(rows[1])
        repeat[COL['ngay']] = '26/10/2026'
        rows.append(repeat)
        plans, _, errors = plan(rows)
        self.assertEqual(errors, ['Dòng 2: tin đã dùng ngày 26/10; bỏ qua tin cũ'])
        self.assertNotIn('230', [s['number'] for s in plans['tong-hop'][0]['stories']])

    def test_freshness_window_is_configurable(self):
        rows = template_rows()
        stories, _, errors = approved_stories(rows_to_dicts(rows), DAY, {'anchor_hour': 7, 'max_age_hours': 6})
        self.assertEqual(len(stories), 4)  # cutoff 01:00 27/10 drops only the 20:15 story of the 26th
        self.assertIn('Dòng 2', errors[0])

    def test_missing_column(self):
        with self.assertRaisesRegex(SheetError, 'Thiếu cột'):
            rows_to_dicts([r[:3] for r in template_rows()])

    def test_spoken_dates(self):
        self.assertEqual(spoken_date('2026-10-27'), 'ngày hai mươi bảy tháng mười')
        self.assertEqual(spoken_date('2026-04-05'), 'ngày mùng năm tháng tư')
        self.assertEqual(spoken_date('2026-11-21'), 'ngày hai mươi mốt tháng mười một')
        self.assertEqual([number_words(n) for n in (14, 15, 24, 25, 30)],
                         ['mười bốn', 'mười lăm', 'hai mươi tư', 'hai mươi lăm', 'ba mươi'])


if __name__ == '__main__':
    unittest.main()

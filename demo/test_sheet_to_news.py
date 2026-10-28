import csv
import json
import os
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from unittest import mock
import auto_render, run_daily
from edition import default_programmes, normalise, number_words, spoken_date
from sheet_to_news import SheetError, approved_stories, canonical_url, parse_date, plan_programmes, rows_to_dicts

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

    def test_url_variants_count_as_the_same_story(self):
        self.assertEqual(canonical_url('https://www.vnexpress.net/a-b-123.html?utm_source=fb#top'),
                         canonical_url('http://vnexpress.net/a-b-123.html/'))
        self.assertNotEqual(canonical_url('https://x.vn/tin?id=1'), canonical_url('https://x.vn/tin?id=2'))
        rows = template_rows()
        repeat = list(rows[1])
        repeat[COL['ngay']] = '26/10/2026'
        repeat[COL['url']] = repeat[COL['url']].replace('https://', 'http://') + '?utm_medium=social'
        rows.append(repeat)
        _, _, errors = plan(rows)
        self.assertEqual(errors, ['Dòng 2: tin đã dùng ngày 26/10; bỏ qua tin cũ'])

    def test_same_event_from_another_outlet_is_dropped(self):
        rows = template_rows()
        other = list(rows[1])  # same figure and headline, different article
        other[COL['url']] = 'https://example.vn/bai-khac.html'
        other[COL['tieu_de_2']] = other[COL['tieu_de_2']] + ' mới'
        rows.append(other)
        _, _, errors = plan(rows)
        self.assertEqual(errors, ['Dòng 7: trùng sự kiện với dòng 2 (cùng số liệu, tiêu đề gần giống); bỏ qua'])
        older = list(other)  # the same event aired yesterday
        older[COL['ngay']] = '26/10/2026'
        rows[-1] = older
        _, _, errors = plan(rows)
        self.assertIn('Dòng 2: trùng sự kiện với dòng 7 ngày 26/10', errors[0])

    def test_same_figure_with_a_different_headline_is_kept(self):
        rows = template_rows()
        other = list(rows[1])
        other[COL['url']] = 'https://example.vn/bai-khac.html'
        other[COL['tieu_de_1']], other[COL['tieu_de_2']] = 'Giá vàng lập đỉnh', 'cao nhất lịch sử'
        rows.append(other)
        _, _, errors = plan(rows)
        self.assertEqual(errors, [])

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


class AutoRenderTests(unittest.TestCase):
    def test_failed_sheet_is_retried_a_limited_number_of_times(self):
        self.assertTrue(auto_render.should_render({}, 'a', 2))
        self.assertFalse(auto_render.should_render({'fingerprint': 'a'}, 'a', 2))  # older state files have no "ok"
        self.assertTrue(auto_render.should_render({'fingerprint': 'a', 'ok': False, 'attempts': 1}, 'a', 2))
        self.assertFalse(auto_render.should_render({'fingerprint': 'a', 'ok': False, 'attempts': 2}, 'a', 2))
        self.assertTrue(auto_render.should_render({'fingerprint': 'a', 'ok': False, 'attempts': 2}, 'b', 2))

    def test_cleanup_keeps_recent_days_and_recently_used_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            daily, work, assets = tmp / 'daily', tmp / 'work', tmp / 'assets'
            for base in (daily, work):
                for d in ('2026-10-01', '2026-10-20'):
                    (base / d).mkdir(parents=True)
            (work / 'pipeline.lock').touch()
            assets.mkdir()
            old, recent, other = assets / 'img-old.jpg', assets / 'vieneu-new.wav', assets / 'bg-1-frame.jpg'
            for f in (old, recent, other):
                f.touch()
            now = time.time()
            os.utime(old, (now - 20 * 86400,) * 2)
            os.utime(other, (now - 20 * 86400,) * 2)
            with mock.patch.multiple(auto_render, DAILY=daily, WORK=work, ASSETS=assets):
                removed = auto_render.cleanup(date(2026, 10, 28), 14, now)
            self.assertEqual(sorted(removed), ['2026-10-01', '2026-10-01', 'img-old.jpg'])
            self.assertEqual(sorted(p.name for p in daily.iterdir()), ['2026-10-20'])
            self.assertTrue(recent.exists() and other.exists())


class RunDailyTests(unittest.TestCase):
    def test_videos_no_longer_planned_are_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            work, out = Path(tmp) / 'work', Path(tmp) / 'out'
            (work / '2026-10-28').mkdir(parents=True)
            (work / '2026-10-28' / 'plan.json').write_text(json.dumps({'programmes': [
                {'id': 'tong-hop', 'status': 'ready'}, {'id': 'cong-nghe', 'status': 'skipped'}]}))
            out.mkdir()
            names = ['tong-hop.mp4', 'tong-hop-frame-1.jpg', 'cong-nghe.mp4', 'cong-nghe-nguon.md',
                     'cong-nghe-frame-3.jpg', 'cong-nghe-preview.png']
            for n in names:
                (out / n).touch()
            with mock.patch.object(run_daily, 'WORK', work):
                run_daily.remove_stale_videos('2026-10-28', out)
            self.assertEqual(sorted(p.name for p in out.iterdir()), ['tong-hop-frame-1.jpg', 'tong-hop.mp4'])


if __name__ == '__main__':
    unittest.main()

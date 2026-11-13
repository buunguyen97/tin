"""Run from demo/: ../.venv-demo/Scripts/python.exe -m unittest test_hot_batches"""
import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

import sheet_to_news, tiktok_post

DAY = date(2026, 11, 14)


def story(n, hot=True, very_hot=False, hot_order=None):
    return {'story': {'url': f'https://vnexpress.net/tin-{n}.html', 'title': [f'Tin {n}'], 'script': 'Một câu.', 'source': 'VnExpress • 14/11/2026'},
            'row': n, 'sector': 'thoi_su', 'sector_label': 'Thời sự', 'hot': hot, 'very_hot': very_hot,
            'order': n, 'hot_order': hot_order if hot_order is not None else n}


class Batches(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(sheet_to_news, 'WORK', Path(self.tmp.name))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def assign(self, stories, hour, minute=0):
        return sheet_to_news.assign_batches(stories, DAY, datetime(2026, 11, 14, hour, minute))

    def test_each_scan_makes_its_own_batch_and_keeps_it(self):
        morning = [story(1), story(2), story(3)]
        state = self.assign(morning, 7, 20)
        self.assertEqual([b['id'] for b in state['batches']], ['0720'])
        noon = morning + [story(4), story(5)]
        state = self.assign(noon, 11, 50)
        self.assertEqual([b['id'] for b in state['batches']], ['0720', '1150'])
        self.assertEqual(len(state['batches'][0]['urls']), 3)  # the morning video keeps its stories
        self.assertEqual(len(state['batches'][1]['urls']), 2)

    def test_one_new_story_waits_for_the_next_scan(self):
        self.assign([story(1), story(2)], 7)
        state = self.assign([story(1), story(2), story(3)], 11)
        self.assertEqual(len(state['batches']), 1)
        self.assertEqual(len(state['waiting']), 1)
        state = self.assign([story(1), story(2), story(3), story(4)], 16)
        self.assertEqual(len(state['batches'][1]['urls']), 2)  # the one that waited goes with the next scan's
        self.assertEqual(state['waiting'], [])

    def test_a_very_hot_story_does_not_wait(self):
        state = self.assign([story(1, very_hot=True)], 9)
        self.assertEqual(len(state['batches']), 1)

    def test_the_last_scan_does_not_wait(self):
        state = self.assign([story(1)], 20)
        self.assertEqual(len(state['batches']), 1)

    def test_stories_that_are_not_hot_make_no_video(self):
        state = self.assign([story(1, hot=False), story(2, hot=False)], 7)
        self.assertEqual(state['batches'], [])

    def test_programmes_one_per_batch_and_one_per_very_hot_story(self):
        stories = [story(1), story(2, very_hot=True), story(3)]
        state = self.assign(stories, 12, 5)
        progs = sheet_to_news.batch_programmes(state, stories, {'programmes': [{'id': 'tong-hop', 'filter': {'hot': True}, 'accent': '#FFD21F'}]})
        self.assertEqual([p['id'] for p in progs], ['tin-nong-1205', 'nong-1205-1'])
        self.assertEqual(progs[0]['title'], 'TIN NÓNG TRƯA')
        self.assertEqual(len(progs[0]['filter']['urls']), 2)  # the very hot story has its own video, not a second place here
        self.assertEqual(progs[0]['accent'], '#FFD21F')
        plans = sheet_to_news.plan_programmes(stories, progs, DAY, {})
        self.assertEqual([len(e['stories']) for _, e, _ in plans], [2, 1])

    def test_a_batch_video_goes_by_hot_order(self):
        stories = [story(1, hot_order=3), story(2, hot_order=1), story(3, hot_order=2)]
        state = self.assign(stories, 7)
        plans = sheet_to_news.plan_programmes(stories, sheet_to_news.batch_programmes(state, stories, {}), DAY, {})
        self.assertEqual([s['title'][0] for s in plans[0][1]['stories']], ['Tin 2', 'Tin 3', 'Tin 1'])


class LateFlush(unittest.TestCase):
    def test_waiting_stories_get_their_video_once_late_in_the_day(self):
        import auto_render
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '2026-11-14-dot.json'
            path.write_text(json.dumps({'batches': [], 'waiting': ['x']}))
            with mock.patch.object(auto_render, 'batches_file', lambda day: path):
                late = lambda hour, state: auto_render.late_flush({'hot_batches': True}, DAY, datetime(2026, 11, 14, hour), state)
                self.assertFalse(late(15, {}))
                self.assertTrue(late(19, {}))
                self.assertFalse(late(20, {'flushed': True}))
                path.write_text(json.dumps({'batches': [], 'waiting': []}))
                self.assertFalse(late(20, {}))

    def test_old_batch_files_are_cleaned(self):
        import auto_render
        with tempfile.TemporaryDirectory() as tmp:
            work, daily, assets = Path(tmp) / 'work', Path(tmp) / 'daily', Path(tmp) / 'assets'
            for d in (work, daily, assets):
                d.mkdir()
            (work / '2026-11-01-dot.json').write_text('{}')
            (work / '2026-11-13-dot.json').write_text('{}')
            with mock.patch.multiple(auto_render, DAILY=daily, WORK=work, ASSETS=assets):
                auto_render.cleanup(date(2026, 11, 14), 7)
            self.assertEqual(sorted(f.name for f in work.iterdir()), ['2026-11-13-dot.json'])


class Posting(unittest.TestCase):
    def test_very_hot_first_then_the_newest_scan(self):
        videos = [{'id': i} for i in ('tin-nong-0720', 'tin-nong-1150', 'nong-1150-1', 'tin-nong-1610')]
        self.assertEqual(tiktok_post.build_order({}, videos), ['nong-1150-1', 'tin-nong-1610', 'tin-nong-1150', 'tin-nong-0720'])

    def test_old_news_is_not_posted(self):
        self.assertTrue(tiktok_post.stale('tin-nong-0720', datetime(2026, 11, 14, 14, 0)))
        self.assertFalse(tiktok_post.stale('tin-nong-1150', datetime(2026, 11, 14, 14, 0)))
        self.assertFalse(tiktok_post.stale('tong-hop', datetime(2026, 11, 14, 23, 0)))

    def test_caption_tag(self):
        edition = {'title': 'TIN NÓNG TRƯA', 'programme': 'tin-nong-1150', 'stories': [{'title': ['Tin'], 'source': 'VnExpress • 14/11/2026'}]}
        self.assertIn('#tinnong', tiktok_post.news_caption(edition))


if __name__ == '__main__':
    unittest.main()

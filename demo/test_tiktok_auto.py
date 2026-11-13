"""The day's automatic posts: first the round-up, then the rest with random gaps, each at most once."""
import json, os, tempfile, unittest
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

import telegram_bot
import tiktok_post
from tiktok_post import PostError


class AutoTest(unittest.TestCase):
    OWNER = 1
    DAY = date.today().isoformat()
    NOW = datetime.combine(date.today(), datetime.min.time()).replace(hour=9)

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.work, self.daily = self.root / 'work', self.root / 'daily'
        (self.work / self.DAY).mkdir(parents=True)
        (self.daily / self.DAY).mkdir(parents=True)
        self.conf = {'tiktok_account': 'acc', 'tiktok_ai_label': False, 'tiktok_auto': True, 'tiktok_auto_from': self.DAY}
        self.free = True
        patches = [mock.patch.object(tiktok_post, 'WORK', self.work), mock.patch.object(tiktok_post, 'DAILY', self.daily),
                   mock.patch.object(tiktok_post, 'REUP_WORK', self.root / 'reup-work'),
                   mock.patch('reup.settings', side_effect=lambda: self.conf), mock.patch('telegram_bot.pipeline_lock', side_effect=self.lock),
                   mock.patch('tiktok_post.prepare_caption')]  # no Claude in these tests
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.bot = telegram_bot.Bot('token', str(self.OWNER))
        self.bot.call = mock.Mock(return_value={'chat': {'id': self.OWNER}, 'message_id': 5})
        for pid in ('a', 'b', 'tong-hop', 'c'):
            self.render(pid, f'Tin {pid}')
        self.plan(['a', 'b', 'tong-hop', 'c'])
        self.settle()

    @contextmanager
    def lock(self):
        yield self.free

    def plan(self, ids):
        (self.work / self.DAY / 'plan.json').write_text(json.dumps({'programmes': [{'id': i, 'status': 'ready'} for i in ids]}), encoding='utf8')

    def render(self, pid, title):
        folder = self.work / self.DAY / pid
        folder.mkdir(exist_ok=True)
        edition = {'date': self.DAY, 'programme': pid, 'title': 'ĐIỂM TIN', 'stories': [{'title': [title], 'script': title, 'source': 'VnExpress • 07/11/2026'}]}
        (folder / 'news.json').write_text(json.dumps(edition), encoding='utf8')
        path = self.daily / self.DAY / f'{pid}.mp4'
        path.write_bytes(b'video')
        stamp = (folder / 'news.json').stat().st_mtime + 1
        os.utime(path, (stamp, stamp))

    def settle(self, when=None):
        """Date the finished render so that it counts as final at NOW."""
        state = self.work / self.DAY / 'auto-render.json'
        state.write_text(json.dumps({'ok': True}), encoding='utf8')
        stamp = (when or self.NOW - timedelta(hours=1)).timestamp()
        os.utime(state, (stamp, stamp))

    def videos(self):
        return tiktok_post.news_videos(self.DAY)

    def pick(self, now=None):
        return tiktok_post.next_auto(self.DAY, self.videos(), now or self.NOW)

    def posted(self, item, at):
        history = tiktok_post.read_history(item['history'])
        history[f"public:{item['ref']}"] = {'video_id': '1', 'at': at.isoformat(timespec='seconds')}
        item['history'].write_text(json.dumps(history), encoding='utf8')

    def state(self):
        return tiktok_post.read_auto(self.DAY)

    def next_at(self):
        return datetime.fromisoformat(self.state()['next_at'])

    def start_over(self):
        tiktok_post.auto_file(self.DAY).unlink(missing_ok=True)

    def queued(self):
        jobs = []
        while not self.bot.jobs.empty():
            jobs.append(self.bot.jobs.get_nowait())
        return jobs

    def said(self, words):
        return [c for c in self.bot.call.call_args_list if words in str(c.kwargs.get('text'))]

    def offers(self):
        return [c for c in self.bot.call.call_args_list if 'reply_markup' in c.kwargs and c.args[0] in ('sendMessage', 'sendPhoto')]

    # --- order ---------------------------------------------------------------------------------------

    def test_round_up_goes_first(self):
        for _ in range(20):
            self.start_over()
            self.assertEqual(self.pick()['id'], 'tong-hop')

    def test_other_videos_follow_in_a_shuffled_order(self):
        orders = set()
        for _ in range(30):
            self.start_over()
            self.pick()
            orders.add(tuple(self.state()['order']))
        self.assertTrue(all(o[0] == 'tong-hop' and sorted(o[1:]) == ['a', 'b', 'c'] for o in orders))
        self.assertGreater(len(orders), 1)

    def test_starts_with_another_video_if_there_is_no_round_up(self):
        self.plan(['a', 'b'])
        self.assertIn(self.pick()['id'], ('a', 'b'))

    def test_all_videos_go_out_once_then_nothing(self):
        now, seen = self.NOW, []
        for _ in range(4):
            item = self.pick(now)
            seen.append(item['id'])
            self.posted(item, now)
            now = self.next_at()
        self.assertEqual(seen[0], 'tong-hop')
        self.assertEqual(sorted(seen), ['a', 'b', 'c', 'tong-hop'])
        self.assertIsNone(self.pick(now))

    # --- timing --------------------------------------------------------------------------------------

    def test_waits_until_the_render_has_settled(self):
        self.settle(self.NOW - timedelta(minutes=29))
        self.assertIsNone(self.pick())
        self.settle(self.NOW - timedelta(minutes=31))
        self.assertIsNotNone(self.pick())

    def test_a_new_render_under_way_stops_everything(self):
        (self.work / self.DAY / 'auto-render.json').unlink()
        self.assertIsNone(self.pick())

    def test_gap_is_random_between_90_and_120_minutes(self):
        gaps = set()
        for _ in range(30):
            self.start_over()
            self.pick()
            gaps.add((self.next_at() - self.NOW).total_seconds())
        self.assertTrue(all(90 * 60 <= g <= 120 * 60 for g in gaps))
        self.assertGreater(len(gaps), 10)

    def test_nothing_before_the_booked_time(self):
        first = self.pick()
        self.posted(first, self.NOW)
        due = self.next_at()
        self.assertIsNone(self.pick(due - timedelta(seconds=1)))
        self.assertNotEqual(self.pick(due)['id'], first['id'])

    def test_a_second_check_does_not_pick_while_the_first_is_uploading(self):
        self.pick()
        self.assertIsNone(self.pick(self.NOW + timedelta(minutes=1)))

    def test_a_restart_keeps_the_schedule(self):
        self.posted(self.pick(), self.NOW)
        order, due = self.state()['order'], self.next_at()
        self.bot = telegram_bot.Bot('token', str(self.OWNER))
        self.assertIsNone(self.pick(due - timedelta(minutes=1)))
        self.assertEqual(self.state()['order'], order)

    def test_a_reup_posted_by_hand_pushes_the_next_video_back(self):
        self.posted(self.pick(), self.NOW)
        folder = tiktok_post.REUP_WORK / 'x'
        folder.mkdir(parents=True)
        by_hand = self.NOW + timedelta(minutes=100)
        (folder / 'tiktok.json').write_text(json.dumps({'public:1': {'video_id': '1', 'at': by_hand.isoformat()}}), encoding='utf8')
        self.assertIsNone(self.pick(by_hand + timedelta(minutes=89)))
        self.assertIsNotNone(self.pick(by_hand + timedelta(minutes=91)))

    def test_stops_at_23(self):
        self.assertIsNone(self.pick(self.NOW.replace(hour=23)))
        self.assertIsNotNone(self.pick(self.NOW.replace(hour=22, minute=59)))

    def test_leftovers_are_reported_once(self):
        with mock.patch('tiktok_post.leftovers', return_value=['a']), mock.patch('tiktok_post.next_auto', return_value=None):
            self.bot.offer_news()
            self.bot.offer_news()
        self.assertEqual(len(self.said('không đăng')), 1)

    # --- daily limit ---------------------------------------------------------------------------------

    def test_no_daily_cap(self):
        self.assertIsNone(tiktok_post.daily_limit('2026-11-07', '2026-11-13'))
        self.assertIsNotNone(tiktok_post.next_auto(self.DAY, self.videos(), self.NOW, limit=None))

    def test_stops_at_the_limit_and_reports_the_rest(self):
        now = self.NOW
        for _ in range(2):
            self.posted(tiktok_post.next_auto(self.DAY, self.videos(), now, limit=2), now)
            now = self.next_at()
        self.assertIsNone(tiktok_post.next_auto(self.DAY, self.videos(), now, limit=2))
        self.assertEqual(len(tiktok_post.leftovers(self.DAY, self.videos(), now, limit=2)), 2)

    def test_round_up_is_inside_the_limit(self):
        self.assertEqual(tiktok_post.next_auto(self.DAY, self.videos(), self.NOW, limit=1)['id'], 'tong-hop')

    def test_a_reup_posted_by_hand_counts_towards_the_limit(self):
        folder = tiktok_post.REUP_WORK / 'x'
        folder.mkdir(parents=True)
        (folder / 'tiktok.json').write_text(json.dumps({'public:1': {'video_id': '1', 'at': (self.NOW - timedelta(hours=3)).isoformat()}}), encoding='utf8')
        self.assertIsNone(tiktok_post.next_auto(self.DAY, self.videos(), self.NOW, limit=1))

    def test_a_refused_video_does_not_use_up_the_limit(self):
        first = tiktok_post.next_auto(self.DAY, self.videos(), self.NOW, limit=1)
        tiktok_post.auto_failed(self.DAY, first['id'], PostError('5'), self.NOW)
        self.assertEqual(tiktok_post.posts_today(self.DAY), 0)

    def test_bot_says_when_the_day_is_full(self):
        self.conf['tiktok_auto_from'] = self.DAY
        self.enterContext(mock.patch.object(tiktok_post, 'DAILY_LIMIT', 3))
        for video in self.videos()[:3]:
            self.posted(video, self.NOW)
        self.bot.offer_news()
        self.assertEqual(len(self.said('Đã đủ 3 bài')), 1)
        self.assertEqual(self.queued(), [])

    def test_schedule_view_marks_what_will_not_go_out(self):
        self.posted(tiktok_post.next_auto(self.DAY, self.videos(), self.NOW, limit=1), self.NOW)
        view = tiktok_post.auto_view(self.DAY, self.videos(), 1)
        self.assertEqual({r['state'] for r in view['rows'][1:]}, {'limited'})
        self.assertIsNone(view['next_at'])
        self.assertEqual((view['count'], view['limit']), (1, 1))

    # --- reviewing before posting ---------------------------------------------------------------------

    def test_order_is_drawn_when_the_render_finishes_and_then_kept(self):
        tiktok_post.fix_order(self.DAY, self.videos())
        order = self.state()['order']
        self.assertEqual(order[0], 'tong-hop')
        tiktok_post.fix_order(self.DAY, self.videos())
        self.pick()
        self.assertEqual(self.state()['order'], order)

    def test_view_shows_which_videos_go_out_today(self):
        tiktok_post.fix_order(self.DAY, self.videos())
        rows = tiktok_post.auto_view(self.DAY, self.videos(), 2)['rows']
        self.assertEqual([r['state'] for r in rows], ['waiting', 'waiting', 'limited', 'limited'])
        self.assertEqual(tiktok_post.auto_view(self.DAY, self.videos(), 2)['next_id'], 'tong-hop')

    def test_skipped_video_stays_out_and_the_next_takes_its_place(self):
        tiktok_post.fix_order(self.DAY, self.videos())
        tiktok_post.set_skipped(self.DAY, 'tong-hop', True)
        self.assertNotEqual(self.pick()['id'], 'tong-hop')
        view = tiktok_post.auto_view(self.DAY, self.videos(), 2)
        self.assertEqual(view['rows'][0]['state'], 'skipped')
        self.assertEqual(sum(r['state'] == 'waiting' for r in view['rows']), 2)

    def test_skipped_video_can_come_back(self):
        tiktok_post.set_skipped(self.DAY, 'tong-hop', True)
        tiktok_post.set_skipped(self.DAY, 'tong-hop', False)
        self.assertEqual(self.pick()['id'], 'tong-hop')

    def test_skipping_stops_a_post_already_picked(self):
        tiktok_post.set_skipped(self.DAY, 'a', True)
        with mock.patch('tiktok_post.post_news') as send:
            self.bot.tiktok_news(self.OWNER, self.DAY, 'a', 'x', False, True)
        send.assert_not_called()

    def test_a_saved_caption_goes_out(self):
        item = self.videos()[0]
        tiktok_post.save_caption(item, 'Caption viết tay')
        self.assertEqual(self.videos()[0]['caption'], 'Caption viết tay')

    def test_a_saved_caption_is_not_written_over_and_lasts_for_that_version(self):
        tiktok_post.fix_order(self.DAY, self.videos())
        first = self.videos()[0]
        tiktok_post.save_caption(first, 'Caption viết tay')
        self.render(first['id'], 'Tin đã đổi')
        self.assertNotEqual(self.videos()[0]['caption'], 'Caption viết tay')

    def test_captions_are_written_only_for_videos_going_out(self):
        tiktok_post.fix_order(self.DAY, self.videos())
        order = self.state()['order']
        with mock.patch('tiktok_post.prepare_caption') as write:
            tiktok_post.prepare_next_caption(self.DAY, self.videos(), 1)
            write.assert_called_once_with(self.DAY, order[0])
            first = next(v for v in self.videos() if v['id'] == order[0])
            tiktok_post.save_caption(first, 'x')
            write.reset_mock()
            tiktok_post.prepare_next_caption(self.DAY, self.videos(), 1)
            write.assert_not_called()

    def test_bot_draws_the_order_before_the_first_post(self):
        self.settle(datetime.now())  # finished just now: nothing goes out yet
        self.bot.offer_news()
        self.assertEqual(self.queued(), [])
        self.assertEqual(self.state()['order'][0], 'tong-hop')

    # --- keeping the machine awake --------------------------------------------------------------------

    def needed(self, hour, limit=3):
        return tiktok_post.machine_needed(self.DAY, self.videos(), limit, self.NOW.replace(hour=hour))

    def test_awake_while_videos_wait(self):
        self.assertTrue(self.needed(9))
        self.assertTrue(self.needed(22))

    def test_may_sleep_at_night(self):
        self.assertFalse(self.needed(6))
        self.assertFalse(self.needed(23))

    def test_may_sleep_once_the_limit_is_reached(self):
        for video in self.videos()[:3]:
            self.posted(video, self.NOW)
        self.assertFalse(self.needed(14))

    def test_may_sleep_once_everything_is_posted(self):
        for video in self.videos():
            self.posted(video, self.NOW)
        with mock.patch.object(tiktok_post, 'hot_news', return_value=False):  # sector videos: no scan comes later
            self.assertFalse(self.needed(14, limit=99))
        self.assertTrue(self.needed(14, limit=99))  # hot-news scans: the afternoon's video is still to come

    def test_awake_for_the_render_in_the_morning_only(self):
        (self.work / self.DAY / 'auto-render.json').unlink()
        self.assertTrue(self.needed(8))
        self.assertTrue(self.needed(13))  # later scans of the day still bring videos
        self.assertFalse(self.needed(21))

    def test_bot_does_not_hold_the_machine_when_off_or_not_started(self):
        for change in ({'tiktok_auto': False}, {'tiktok_account': ''}, {'tiktok_auto_from': '2999-01-01'}):
            self.conf.update(tiktok_auto=True, tiktok_account='acc', tiktok_auto_from=self.DAY)
            self.conf.update(change)
            self.assertFalse(self.bot.machine_needed())

    # --- changed stories -----------------------------------------------------------------------------

    def test_a_programme_goes_out_once_even_if_its_stories_change(self):
        self.posted(self.pick(), self.NOW)
        self.render('tong-hop', 'Tin đã đổi')
        self.settle()
        self.assertNotEqual(self.pick(self.NOW + timedelta(hours=3))['id'], 'tong-hop')

    def test_waiting_video_goes_out_in_its_latest_version(self):
        self.posted(self.pick(), self.NOW)
        wanted = self.state()['order'][1]
        self.render(wanted, 'Bản mới')
        self.settle()
        item = self.pick(self.NOW + timedelta(hours=3))
        self.assertEqual(item['id'], wanted)
        self.assertIn('Bản mới', item['caption'])

    # --- failures ------------------------------------------------------------------------------------

    def test_refused_video_is_retried_twice_then_skipped(self):
        first, now = self.pick(), self.NOW
        for outcome in ('retry', 'retry', 'skip'):
            self.assertEqual(tiktok_post.auto_failed(self.DAY, first['id'], PostError('5'), now), outcome)
            retry_at = self.next_at()
            self.assertTrue(now + timedelta(minutes=30) <= retry_at <= now + timedelta(minutes=45))
            now = retry_at
            item = self.pick(now)
            self.assertEqual(item['id'] == first['id'], outcome == 'retry')

    def test_a_post_that_may_have_gone_out_is_not_retried(self):
        first = self.pick()
        self.assertEqual(tiktok_post.auto_failed(self.DAY, first['id'], PostError('?', retry=False), self.NOW), 'check')
        self.assertNotEqual(self.pick(self.NOW + timedelta(hours=3))['id'], first['id'])

    def test_login_problem_uses_no_tries_and_warns_once(self):
        first = self.pick()
        for _ in range(5):
            self.bot.auto_news_failed({'chat': {'id': self.OWNER}, 'message_id': 1}, self.DAY, first['id'], PostError('login', stop=True))
        self.assertEqual(self.state()['tries'][first['id']], 0)
        self.assertEqual(len(self.said('chờ bạn xử lý lỗi này')), 1)
        tiktok_post.auto_failed(self.DAY, first['id'], PostError('login', stop=True), self.NOW)  # the bot's calls used the real clock
        self.assertEqual(self.pick(self.NOW + timedelta(hours=3))['id'], first['id'])

    def test_no_account_warns_once_and_posts_nothing(self):
        self.conf['tiktok_account'] = ''
        self.bot.offer_news()
        self.bot.offer_news()
        self.assertEqual(len(self.said('chưa kết nối')), 1)
        self.assertEqual(self.queued(), [])

    # --- switches and the bot ------------------------------------------------------------------------

    def test_does_not_start_before_the_chosen_day(self):
        self.conf['tiktok_auto_from'] = '2999-01-01'
        self.bot.offer_news()
        self.assertEqual(self.queued(), [])
        self.assertFalse(tiktok_post.auto_file(self.DAY).exists())

    def test_off_means_no_automatic_post(self):
        self.conf['tiktok_auto'] = False
        self.bot.offer_news()
        self.assertEqual(self.queued(), [])

    def test_switching_off_stops_a_post_already_picked(self):
        self.conf['tiktok_auto'] = False
        with mock.patch('tiktok_post.post_news') as send:
            self.bot.tiktok_news(self.OWNER, self.DAY, 'a', 'x', False, True)
        send.assert_not_called()

    def test_bot_posts_publicly_and_takes_the_latest_version(self):
        with mock.patch('tiktok_post.post_news', return_value='1') as send:
            self.bot.tiktok_news(self.OWNER, self.DAY, 'a', 'old-key', False, True)
        send.assert_called_once()
        self.assertFalse(send.call_args.args[2])

    def test_no_offers_while_automatic(self):
        self.bot.offer_news()
        self.assertEqual(self.offers(), [])

    def test_a_render_under_way_delays_a_picked_post(self):
        self.free = False
        with mock.patch('tiktok_post.post_news') as send, mock.patch('threading.Timer') as timer:
            self.bot.tiktok_news(self.OWNER, self.DAY, 'a', 'x', False, True)
        send.assert_not_called()
        self.assertTrue(timer.call_args.kwargs['args'][0][-1])

    def test_reup_is_never_posted_by_itself(self):
        wd = self.root / 'reup'
        wd.mkdir()
        (wd / 'script.json').write_text(json.dumps({'title': ['T'], 'caption': 'c'}), encoding='utf8')
        video = wd / 'v.mp4'
        video.write_bytes(b'x')
        with mock.patch('reup.produce', return_value=video), mock.patch('reup.smaller_copy', return_value=video), \
                mock.patch('tiktok_post.post_reup') as send:
            self.bot.make(self.OWNER, wd)
        send.assert_not_called()
        self.assertEqual(len(self.offers()), 1)

    # --- reups sent to the bot ------------------------------------------------------------------

    def news_off(self):
        self.conf['tiktok_auto'] = False

    def test_reups_sent_together_get_their_own_slots(self):
        times = [tiktok_post.book_reup(n, self.OWNER, self.NOW + timedelta(minutes=i)) for i, n in enumerate('abc')]
        self.assertEqual(times, [self.NOW, self.NOW + timedelta(minutes=30), self.NOW + timedelta(minutes=60)])

    def test_a_reup_slot_counts_from_the_last_post(self):
        self.posted(self.pick(), self.NOW)
        self.assertEqual(tiktok_post.book_reup('a', self.OWNER, self.NOW + timedelta(minutes=10)), self.NOW + timedelta(minutes=30))

    def test_reups_go_out_one_at_a_time(self):
        self.news_off()
        for n in 'ab':
            tiktok_post.book_reup(n, self.OWNER, self.NOW)
        self.assertEqual(tiktok_post.due_reup(self.NOW)['name'], 'a')
        self.assertIsNone(tiktok_post.due_reup(self.NOW + timedelta(minutes=29)))  # a may still be uploading
        self.assertEqual(tiktok_post.due_reup(self.NOW + timedelta(minutes=30))['name'], 'b')
        self.assertIsNone(tiktok_post.due_reup(self.NOW + timedelta(hours=2)))

    def test_waiting_news_goes_before_a_reup(self):
        tiktok_post.book_reup('a', self.OWNER, self.NOW)
        self.assertIsNone(tiktok_post.due_reup(self.NOW))
        self.news_off()  # as if every news video had gone out
        self.assertEqual(tiktok_post.due_reup(self.NOW)['name'], 'a')

    def test_a_late_reup_waits_for_the_morning(self):
        self.news_off()
        night = self.NOW.replace(hour=23, minute=30)
        at = tiktok_post.book_reup('a', self.OWNER, night)
        self.assertEqual(at, (night + timedelta(days=1)).replace(hour=7, minute=0))
        self.assertIsNone(tiktok_post.due_reup(night + timedelta(minutes=40)))
        self.assertEqual(tiktok_post.due_reup(at)['name'], 'a')

    def test_bot_queues_a_reup_and_the_watcher_posts_it(self):
        self.news_off()
        self.bot.post_soon(self.OWNER, self.root / 'r1', self.NOW)
        self.bot.post_soon(self.OWNER, self.root / 'r2', self.NOW)
        self.assertIn('khoảng 09:30', self.said('sẽ tự đăng')[0].kwargs['text'])
        self.bot.post_reups(self.NOW)
        self.assertEqual(self.bot.jobs.get_nowait()[:3], ('tiktok', self.OWNER, telegram_bot.reup.REUP / 'r1'))
        self.bot.post_reups(self.NOW + timedelta(minutes=1))
        self.assertTrue(self.bot.jobs.empty())

    def test_queued_reups_keep_the_machine_awake(self):
        self.news_off()
        tiktok_post.book_reup('a', self.OWNER, self.NOW)
        with mock.patch.object(tiktok_post, 'reups_waiting', return_value=True):
            self.assertTrue(self.bot.machine_needed())

    def test_schedule_view_for_studio(self):
        self.posted(self.pick(), self.NOW)
        view = tiktok_post.auto_view(self.DAY, self.videos())
        self.assertEqual(view['rows'][0], {'id': 'tong-hop', 'state': 'posted', 'at': self.NOW.isoformat(timespec='seconds'), 'message': None})
        self.assertEqual({r['state'] for r in view['rows'][1:]}, {'waiting'})
        self.assertEqual(view['next_at'], self.state()['next_at'])


if __name__ == '__main__':
    unittest.main()

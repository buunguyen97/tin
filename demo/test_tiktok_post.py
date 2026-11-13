import json, os, tempfile, threading, time, unittest, urllib.error, urllib.request
from contextlib import contextmanager
from datetime import date
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import autotok
import autotok.errors as errors

import studio
import telegram_bot
import tiktok_post
from tiktok_post import PostError, post_reup


class Folder(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.wd = Path(folder.name)
        (self.wd / 'script.json').write_text(json.dumps({'title': ['Tin', 'nóng'], 'caption': 'Chú thích #tin'}), encoding='utf8')
        self.video = self.wd / 'clip.mp4'
        self.video.write_bytes(b'video')


class PostTest(Folder):
    def client(self, upload):
        patcher = mock.patch('autotok.Client.from_account', return_value=SimpleNamespace(upload=upload))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_posts_the_scripts_caption_publicly(self):
        upload = mock.Mock(return_value=SimpleNamespace(video_id='123'))
        self.client(upload)
        self.assertEqual(post_reup(self.wd,self.video, 'acc'), '123')
        upload.assert_called_once_with(self.video, 'Chú thích #tin', visibility='public', ai_label=False)

    def test_private_goes_out_private(self):
        upload = mock.Mock(return_value=SimpleNamespace(video_id='1'))
        self.client(upload)
        post_reup(self.wd,self.video, 'acc', private=True)
        self.assertEqual(upload.call_args.kwargs['visibility'], 'private')

    def test_title_stands_in_for_a_missing_caption(self):
        self.assertEqual(tiktok_post.caption({'title': ['Tin', 'nóng'], 'caption': ''}), 'Tin nóng')

    def test_caption_is_cut_to_tiktoks_limit(self):
        self.assertEqual(len(tiktok_post.caption({'caption': 'x' * 3000})), tiktok_post.CAPTION_LIMIT)

    def test_same_render_never_posts_twice(self):
        upload = mock.Mock(return_value=SimpleNamespace(video_id='1'))
        self.client(upload)
        post_reup(self.wd,self.video, 'acc')
        with self.assertRaises(PostError) as caught:
            post_reup(self.wd,self.video, 'acc')
        self.assertFalse(caught.exception.retry)
        upload.assert_called_once()

    def test_private_post_does_not_block_the_public_one(self):
        upload = mock.Mock(return_value=SimpleNamespace(video_id='1'))
        self.client(upload)
        post_reup(self.wd,self.video, 'acc', private=True)
        post_reup(self.wd,self.video, 'acc')
        self.assertEqual(upload.call_count, 2)

    def test_new_render_may_post_again(self):
        upload = mock.Mock(return_value=SimpleNamespace(video_id='1'))
        self.client(upload)
        post_reup(self.wd,self.video, 'acc')
        history = tiktok_post.read_history(self.wd / 'tiktok.json')
        (self.wd / 'tiktok.json').write_text(json.dumps({k.replace(':', ':old'): v for k, v in history.items()}), encoding='utf8')
        post_reup(self.wd,self.video, 'acc')
        self.assertEqual(upload.call_count, 2)

    def test_uncertain_publish_blocks_a_second_try(self):
        upload = mock.Mock(side_effect=errors.PublishUncertainError('timeout'))
        self.client(upload)
        for _ in range(2):
            with self.assertRaises(PostError) as caught:
                post_reup(self.wd,self.video, 'acc')
            self.assertFalse(caught.exception.retry)
        upload.assert_called_once()

    def test_unexpected_error_counts_as_uncertain(self):
        upload = mock.Mock(side_effect=OSError('reset'))
        self.client(upload)
        with self.assertRaises(PostError) as caught:
            post_reup(self.wd,self.video, 'acc')
        self.assertFalse(caught.exception.retry)

    def test_failure_before_publishing_may_retry(self):
        upload = mock.Mock(side_effect=[errors.UploadError('slow'), SimpleNamespace(video_id='9')])
        self.client(upload)
        with self.assertRaises(PostError) as caught:
            post_reup(self.wd,self.video, 'acc')
        self.assertTrue(caught.exception.retry)
        self.assertEqual(post_reup(self.wd,self.video, 'acc'), '9')

    def test_login_problem_says_to_log_in(self):
        with mock.patch('autotok.Client.from_account', side_effect=errors.NotLoggedInError('acc')):
            with self.assertRaises(PostError) as caught:
                post_reup(self.wd,self.video, 'acc')
        self.assertIn('autotok login', str(caught.exception))
        self.assertTrue(caught.exception.retry)

    def test_missing_video(self):
        self.video.unlink()
        with self.assertRaises(PostError):
            post_reup(self.wd,self.video, 'acc')


class BotTest(Folder):
    OWNER, GUEST = 1, 2

    def setUp(self):
        super().setUp()
        self.bot = telegram_bot.Bot('token', f'{self.OWNER},{self.GUEST}')
        self.bot.call = mock.Mock(return_value={'chat': {'id': self.OWNER}, 'message_id': 5})
        self.conf = {'tiktok_account': 'acc', 'tiktok_ai_label': False, 'tiktok_auto': False, 'tiktok_auto_from': ''}
        patcher = mock.patch('reup.settings', side_effect=lambda: self.conf)
        patcher.start()
        self.addCleanup(patcher.stop)

    def press(self, chat, data):
        self.bot.on_button({'id': 'q', 'data': data, 'message': {'chat': {'id': chat}, 'message_id': 7}})

    def buttons(self):
        """The callback data of the buttons in the last sendMessage."""
        sent = [c for c in self.bot.call.call_args_list if c.args[0] == 'sendMessage' and 'reply_markup' in c.kwargs]
        return [b['callback_data'] for row in sent[-1].kwargs['reply_markup']['inline_keyboard'] for b in row] if sent else []

    def test_owner_is_offered_both_buttons(self):
        self.bot.offer_tiktok(self.OWNER, self.wd)
        self.assertEqual(self.buttons(), [f'tt:{self.wd.name}', f'ttp:{self.wd.name}'])

    def test_guest_is_not_offered_the_buttons(self):
        self.bot.offer_tiktok(self.GUEST, self.wd)
        self.assertEqual(self.buttons(), [])

    def test_no_buttons_without_an_account(self):
        self.conf['tiktok_account'] = ''
        self.bot.offer_tiktok(self.OWNER, self.wd)
        self.assertEqual(self.buttons(), [])

    def test_guest_cannot_post_with_a_forged_button(self):
        self.press(self.GUEST, 'tt:abc')
        self.assertTrue(self.bot.jobs.empty())

    def test_owner_button_queues_the_post(self):
        self.press(self.OWNER, 'ttp:abc')
        job = self.bot.jobs.get_nowait()
        self.assertEqual((job[0], job[1], job[2].name, job[3]), ('tiktok', self.OWNER, 'abc', True))

    def test_retryable_failure_offers_the_buttons_again(self):
        with mock.patch('tiktok_post.post_reup', side_effect=PostError('slow')):
            self.bot.tiktok(self.OWNER, self.wd, False)
        self.assertEqual(len(self.buttons()), 2)

    def test_uncertain_failure_offers_no_button(self):
        with mock.patch('tiktok_post.post_reup', side_effect=PostError('maybe', retry=False)):
            self.bot.tiktok(self.OWNER, self.wd, False)
        self.assertEqual(self.buttons(), [])

    def test_public_post_ends_it(self):
        with mock.patch('tiktok_post.post_reup', return_value='1'):
            self.bot.tiktok(self.OWNER, self.wd, False)
        self.assertEqual(self.buttons(), [])

    def test_private_post_leaves_the_public_button(self):
        with mock.patch('tiktok_post.post_reup', return_value='1'):
            self.bot.tiktok(self.OWNER, self.wd, True)
        self.assertEqual(self.buttons(), [f'tt:{self.wd.name}'])


class NewsTest(BotTest):
    DAY = date.today().isoformat()

    def setUp(self):
        super().setUp()
        self.conf['tiktok_account'] = 'news'
        self.work, self.daily = self.wd / 'work', self.wd / 'daily'
        for name, value in (('WORK', self.work), ('DAILY', self.daily)):
            patcher = mock.patch.object(tiktok_post, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        lock = mock.patch('telegram_bot.pipeline_lock', side_effect=self.lock)
        lock.start()
        self.addCleanup(lock.stop)
        writer = mock.patch('tiktok_post.prepare_caption')  # no Claude in these tests
        writer.start()
        self.addCleanup(writer.stop)
        self.free = True
        (self.work / self.DAY).mkdir(parents=True)
        (self.daily / self.DAY).mkdir(parents=True)
        self.render('a', 'Tin một')
        self.render('b', 'Tin hai')
        self.plan(['a', 'b'])
        self.finished(True)

    @contextmanager
    def lock(self):
        yield self.free

    def plan(self, ids):
        (self.work / self.DAY / 'plan.json').write_text(json.dumps({'programmes': [{'id': i, 'status': 'ready'} for i in ids]}), encoding='utf8')

    def finished(self, ok):
        (self.work / self.DAY / 'auto-render.json').write_text(json.dumps({'ok': ok}), encoding='utf8')

    def render(self, pid, title, video=True):
        folder = self.work / self.DAY / pid
        folder.mkdir(exist_ok=True)
        edition = {'date': self.DAY, 'programme': pid, 'title': 'ĐIỂM TIN', 'stories': [{'title': [title, 'mới'], 'script': title, 'source': 'VnExpress • 06/11/2026'}]}
        (folder / 'news.json').write_text(json.dumps(edition), encoding='utf8')
        if video:
            path = self.daily / self.DAY / f'{pid}.mp4'
            path.write_bytes(b'video')
            later = (folder / 'news.json').stat().st_mtime + 1
            os.utime(path, (later, later))

    def offers(self):
        return [c for c in self.bot.call.call_args_list if 'reply_markup' in c.kwargs and c.args[0] in ('sendMessage', 'sendPhoto')]

    def item(self, pid='a'):
        return next(v for v in tiktok_post.news_videos(self.DAY) if v['id'] == pid)

    def test_caption_lists_stories_sources_and_tags(self):
        text = self.item()['caption']
        for part in ('Tin một mới', 'VnExpress', '#tintuc', '#a'):
            self.assertIn(part, text)

    def test_list_waits_on_the_job_thread(self):
        self.bot.on_message({'chat': {'id': self.OWNER, 'type': 'private'}, 'text': '/tin'})
        self.assertEqual(self.bot.jobs.get_nowait(), ('news_list', self.OWNER))

    def test_round_up_gets_no_programme_tag(self):
        self.assertNotIn('#tonghop', tiktok_post.news_caption({'date': self.DAY, 'programme': 'tong-hop', 'title': 'T', 'stories': []}))

    def test_finished_render_is_offered_once(self):
        self.bot.offer_news()
        self.assertEqual(len(self.offers()), 2)
        self.bot.offer_news()
        self.assertEqual(len(self.offers()), 2)

    def test_nothing_offered_while_a_render_is_under_way(self):
        (self.work / self.DAY / 'auto-render.json').unlink()
        self.bot.offer_news()
        self.assertEqual(self.offers(), [])

    def test_failed_render_is_not_offered(self):
        self.finished(False)
        self.bot.offer_news()
        self.assertEqual(self.offers(), [])

    def test_no_account_no_offers(self):
        self.conf['tiktok_account'] = ''
        self.bot.offer_news()
        self.assertEqual(self.offers(), [])

    def test_only_changed_video_is_offered_again(self):
        self.bot.offer_news()
        self.render('a', 'Tin khác')
        self.bot.offer_news()
        self.assertEqual(len(self.offers()), 3)

    def test_same_stories_rendered_again_are_not_offered_again(self):
        self.bot.offer_news()
        self.render('a', 'Tin một')
        self.bot.offer_news()
        self.assertEqual(len(self.offers()), 2)

    def test_video_older_than_its_stories_is_left_out(self):
        self.render('a', 'Tin khác', video=False)
        news = self.work / self.DAY / 'a' / 'news.json'
        later = (self.daily / self.DAY / 'a.mp4').stat().st_mtime + 5
        os.utime(news, (later, later))
        self.assertEqual([v['id'] for v in tiktok_post.news_videos(self.DAY)], ['b'])

    def test_again_repeats_what_is_not_posted_but_skips_what_is(self):
        self.bot.offer_news()
        tiktok_post.remember(tiktok_post.news_history(self.DAY), f"public:a:{self.item()['key']}", video_id='1')
        self.bot.call.reset_mock()
        self.bot.offer_news(again=True)
        self.assertEqual(len(self.offers()), 1)

    def test_guest_cannot_use_news_commands_or_buttons(self):
        self.press(self.GUEST, f"ttn:{self.DAY}/a/{self.item()['key']}")
        self.assertTrue(self.bot.jobs.empty())
        self.bot.on_message({'chat': {'id': self.GUEST, 'type': 'private'}, 'text': '/tin'})
        self.assertEqual(self.offers(), [])

    def test_button_queues_the_right_video(self):
        key = self.item()['key']
        self.press(self.OWNER, f'ttnp:{self.DAY}/a/{key}')
        self.assertEqual(self.bot.jobs.get_nowait(), ('tiktok_news', self.OWNER, self.DAY, 'a', key, True))

    def test_posts_full_video_with_caption(self):
        upload = mock.Mock(return_value=SimpleNamespace(video_id='7'))
        with mock.patch('autotok.Client.from_account', return_value=SimpleNamespace(upload=upload)) as account:
            self.bot.tiktok_news(self.OWNER, self.DAY, 'a', self.item()['key'], False)
        account.assert_called_once_with('news')
        self.assertEqual(upload.call_args.args, (self.item()['video'], self.item()['caption']))
        self.assertTrue(tiktok_post.seen(self.item(), 'public'))

    def test_old_button_does_not_post_a_changed_video(self):
        key = self.item()['key']
        self.render('a', 'Tin khác')
        with mock.patch('tiktok_post.post_news') as send:
            self.bot.tiktok_news(self.OWNER, self.DAY, 'a', key, False)
        send.assert_not_called()

    def test_waits_while_a_render_holds_the_lock(self):
        self.free = False
        with mock.patch('tiktok_post.post_news') as send, mock.patch('threading.Timer') as timer:
            self.bot.tiktok_news(self.OWNER, self.DAY, 'a', self.item()['key'], False)
        send.assert_not_called()
        timer.assert_called_once()


class StudioTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), studio.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]
        self.video = self.root / 'output' / 'abc.mp4'
        self.video.parent.mkdir()
        self.video.write_bytes(b'video')
        (self.root / 'work' / 'abc').mkdir(parents=True)
        (self.root / 'work' / 'abc' / 'script.json').write_text(json.dumps({'title': ['Tin'], 'caption': 'Cũ'}), encoding='utf8')
        self.conf = {'tiktok_account': 'acc', 'tiktok_ai_label': False, 'tiktok_auto': False, 'tiktok_auto_from': ''}
        patches = [mock.patch.object(studio, 'PORT', self.port), mock.patch.object(tiktok_post, 'REUP_OUTPUT', self.video.parent),
                   mock.patch.object(tiktok_post, 'REUP_WORK', self.root / 'work'), mock.patch.object(tiktok_post, 'CONFIG', self.root / 'config.json'),
                   mock.patch('reup.settings', side_effect=lambda: self.conf), mock.patch('tiktok_post.accounts', return_value=['acc'])]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        studio.TikTok.post = {'running': False, 'label': '', 'message': '', 'ok': None}

    def send(self, action, body, **headers):
        request = urllib.request.Request(f'http://127.0.0.1:{self.port}/api/tiktok/{action}', json.dumps(body).encode(), headers)
        try:
            with urllib.request.urlopen(request) as answer:
                return answer.status, json.load(answer)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def key(self):
        return tiktok_post.reup_videos()[0]['key']

    def post_body(self, **changes):
        return {'kind': 'reup', 'id': 'abc', 'key': self.key(), 'private': True, 'caption': 'Mới', **changes}

    def finish_post(self):
        for _ in range(200):
            if not studio.TikTok.post['running']:
                return studio.TikTok.post
            time.sleep(.01)
        self.fail('post did not finish')

    def test_other_web_pages_cannot_post(self):
        with mock.patch('tiktok_post.post_item') as send:
            status, _ = self.send('post', self.post_body(), Origin='https://evil.example')
        self.assertEqual(status, 403)
        send.assert_not_called()

    def test_edited_caption_goes_out(self):
        with mock.patch('tiktok_post.post_item', return_value='9') as send:
            self.assertEqual(self.send('post', self.post_body(), Origin=f'http://127.0.0.1:{self.port}')[0], 200)
            result = self.finish_post()
        self.assertTrue(result['ok'], result)
        self.assertEqual(send.call_args.args[1:], ('acc', True, False, 'Mới'))

    def test_changed_video_is_not_posted(self):
        with mock.patch('tiktok_post.post_item') as send:
            self.send('post', self.post_body(key='old'))
            result = self.finish_post()
        self.assertFalse(result['ok'])
        send.assert_not_called()

    def test_empty_caption_is_refused(self):
        self.assertEqual(self.send('post', self.post_body(caption='  '))[0], 400)

    def test_no_account_is_refused(self):
        self.conf['tiktok_account'] = ''
        self.assertEqual(self.send('post', self.post_body())[0], 400)

    def test_bad_video_id_is_refused(self):
        self.assertEqual(self.send('post', self.post_body(id='../x'))[0], 400)

    def test_bad_account_name_is_refused(self):
        self.assertEqual(self.send('login', {'name': 'a b; rm'})[0], 400)

    def test_settings_keep_the_rest_of_config(self):
        tiktok_post.CONFIG.write_text(json.dumps({'voice': 'Mai Anh', 'reup': {'speed': 1.2}}), encoding='utf8')
        self.assertEqual(self.send('settings', {'auto': True, 'ai_label': True})[0], 200)
        saved = json.loads(tiktok_post.CONFIG.read_text(encoding='utf8'))
        self.assertEqual(saved, {'voice': 'Mai Anh', 'reup': {'speed': 1.2, 'tiktok_account': 'acc', 'tiktok_ai_label': True, 'tiktok_auto': True}})

    def test_other_web_pages_cannot_turn_on_auto(self):
        self.assertEqual(self.send('settings', {'auto': True}, Origin='https://evil.example')[0], 403)
        self.assertFalse(tiktok_post.CONFIG.exists())

    def test_forget_allows_posting_again_after_an_uncertain_result(self):
        item = tiktok_post.reup_videos()[0]
        tiktok_post.remember(item['history'], f"public:{item['ref']}", uncertain=True)
        self.assertEqual(self.send('forget', {'kind': 'reup', 'id': 'abc', 'key': item['key']})[0], 200)
        self.assertIsNone(tiktok_post.status(item)['public'])

    def test_saved_caption_is_kept_for_a_reup(self):
        self.assertEqual(self.send('caption', {'kind': 'reup', 'id': 'abc', 'key': self.key(), 'caption': 'Viết tay'})[0], 200)
        self.assertEqual(tiktok_post.reup_videos()[0]['caption'], 'Viết tay')

    def test_bot_posts_a_reup_with_its_saved_caption(self):
        self.send('caption', {'kind': 'reup', 'id': 'abc', 'key': self.key(), 'caption': 'Viết tay'})
        with mock.patch('tiktok_post.post', return_value='1') as send:
            tiktok_post.post_reup(self.root / 'work' / 'abc', self.video, 'acc')
        self.assertEqual(send.call_args.args[1], 'Viết tay')

    def test_empty_saved_caption_is_refused(self):
        self.assertEqual(self.send('caption', {'kind': 'reup', 'id': 'abc', 'key': self.key(), 'caption': ' '})[0], 400)

    def test_caption_for_a_changed_video_is_refused(self):
        self.assertEqual(self.send('caption', {'kind': 'reup', 'id': 'abc', 'key': 'old', 'caption': 'x'})[0], 409)

    def test_other_web_pages_cannot_save_or_skip(self):
        evil = {'Origin': 'https://evil.example'}
        self.assertEqual(self.send('caption', {'kind': 'reup', 'id': 'abc', 'key': self.key(), 'caption': 'x'}, **evil)[0], 403)
        self.assertEqual(self.send('skip', {'kind': 'news', 'day': '2026-11-07', 'id': 'a', 'skip': True}, **evil)[0], 403)

    def test_reup_cannot_be_skipped(self):
        self.assertEqual(self.send('skip', {'kind': 'reup', 'id': 'abc', 'skip': True})[0], 400)

    def test_forget_keeps_a_finished_post(self):
        item = tiktok_post.reup_videos()[0]
        tiktok_post.remember(item['history'], f"public:{item['ref']}", video_id='1')
        self.send('forget', {'kind': 'reup', 'id': 'abc', 'key': item['key']})
        self.assertEqual(tiktok_post.status(item)['public']['video_id'], '1')


if __name__ == '__main__':
    unittest.main()

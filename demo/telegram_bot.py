"""Telegram bot for demo/reup.py: send a video link, get the MP4 back (and, for the owner, on TikTok).

With "auto_reup" on (the default; /tudong turns it on or off), a link goes straight through: Claude's
script, voice, render, the video sent back, and the owner's video queued for a public TikTok post: one at a
time, tiktok_post.REUP_GAP minutes apart, after any hot-news video waiting, never after STOP_HOUR (it waits for
the morning). Off, the script and the post each wait for a button.

  .venv-demo/Scripts/python.exe demo/telegram_bot.py

Needs TELEGRAM_BOT_TOKEN in .env. Who may use it is TELEGRAM_CHAT_ID: chat ids separated by commas, the
first being the owner. The first chat that sends /start becomes the owner. Anyone else (another account,
or a group the bot was added to) who sends /start is put to the owner, who allows or refuses them with a
button. /ds lists the chats, /xoa <id> removes one.

Jobs run one at a time on this machine; renders wait for the daily videos (shared lock). Long polling:
the machine needs no open port.
"""
import json, queue, re, sys, threading, time, traceback
from datetime import date, datetime, timedelta

import requests

import reup
import tiktok_post
from edition import WORK, pipeline_lock

URL = re.compile(r'https?://\S+')
POLL = 50  # seconds a getUpdates call waits for messages
RETRY_BUSY = 60  # seconds before a render tries the lock again
NEWS_CHECK = 60  # seconds between looks for newly rendered news videos
HELP = ('Gửi link video (X, TikTok, YouTube, Facebook…). Mình tải video, nhờ Claude viết kịch bản, '
        'đọc giọng, render và gửi video lại cho bạn.\n\n'
        'Chế độ tự động (/tudong để bật hoặc tắt): làm video luôn, không cần duyệt, và video của chủ bot tự đăng TikTok công khai. '
        'Khi tắt: bot gửi kịch bản để duyệt; nhắn một câu bất kỳ để yêu cầu sửa, ví dụ "ngắn lại", "bỏ câu thứ ba".')


def log(*parts):
    print(datetime.now().strftime('%Y-%m-%d %H:%M:%S'), *parts, flush=True)


def keep_awake(on):
    """Ask Windows not to sleep on idle, for as long as this thread lives or until asked back. Changes no setting."""
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | (0x00000001 if on else 0))  # ES_CONTINUOUS, ES_SYSTEM_REQUIRED


def who(message):
    """A readable name for the sender and the chat, for the owner's approval request."""
    sender, chat = message.get('from', {}), message['chat']
    name = ' '.join(filter(None, [sender.get('first_name'), sender.get('last_name')])) or 'Không tên'
    if sender.get('username'):
        name += f" (@{sender['username']})"
    return f"nhóm \"{chat.get('title')}\" — {name}" if chat['type'] != 'private' else name


class Bot:
    def __init__(self, token, chats):
        self.base = f'https://api.telegram.org/bot{token}/'
        self.chats = [int(c) for c in chats.replace(' ', '').split(',') if c]  # chats[0] is the owner
        self.jobs = queue.Queue()
        self.current = {}  # chat -> work folder of the script last shown there, which a plain text message revises
        self.asked = set()  # chats already put to the owner

    @property
    def owner(self):
        return self.chats[0] if self.chats else None

    def call(self, method, files=None, wait=60, **params):
        data = {k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in params.items()}
        r = requests.post(self.base + method, data=data, files=files, timeout=wait)
        answer = r.json()
        if not answer.get('ok'):
            raise RuntimeError(f"Telegram {method}: {answer.get('description')}")
        return answer['result']

    def say(self, chat, text, **extra):
        return self.call('sendMessage', chat_id=chat, text=text[:4096], **extra)

    def status(self, message, text):
        """Overwrite a progress message; never let a failed edit stop the job."""
        try:
            self.call('editMessageText', chat_id=message['chat']['id'], message_id=message['message_id'], text=text)
        except Exception as e:
            log('edit failed:', e)

    def save_chats(self):
        """Remember who may use the bot in .env."""
        env = reup.project / '.env'
        lines = env.read_text().splitlines() if env.exists() else []
        lines = [l for l in lines if not l.startswith('TELEGRAM_CHAT_ID=')] + [f"TELEGRAM_CHAT_ID={','.join(map(str, self.chats))}"]
        env.write_text('\n'.join(lines) + '\n')
        log('chats:', self.chats)

    # --- incoming ---------------------------------------------------------------------------------

    def on_message(self, message):
        chat, text = message['chat']['id'], (message.get('text') or message.get('caption') or '').strip()
        command = text.split()[0].split('@')[0].lower() if text.startswith('/') else ''
        if command == '/start' and chat not in self.chats:
            return self.ask_owner(chat, message)
        if chat not in self.chats:
            log('ignored chat', chat)
            return
        if command in ('/start', '/help'):
            return self.say(chat, HELP)
        if command == '/ds' and chat == self.owner:
            return self.say(chat, 'Được dùng bot:\n' + '\n'.join(f"{c}{' (chủ)' if c == self.owner else ''}" for c in self.chats)
                            + '\n\nXoá: /xoa <id>')
        if command == '/xoa' and chat == self.owner:
            ids = [int(x) for x in re.findall(r'-?\d+', text)]
            removed = [c for c in ids if c in self.chats and c != self.owner]
            self.chats = [c for c in self.chats if c not in removed]
            self.save_chats()
            return self.say(chat, f'Đã xoá: {", ".join(map(str, removed))}' if removed else 'Không có id nào để xoá (không xoá được chủ).')
        if command == '/tudong' and chat == self.owner:
            words = text.split()
            on = not reup.settings()['auto_reup'] if len(words) < 2 else words[1].lower() in ('on', 'bat', 'bật', '1')
            set_auto_reup(on)
            return self.say(chat, '🤖 Đã bật tự động: gửi link là làm video và đăng TikTok luôn.' if on
                            else '✋ Đã tắt tự động: bot gửi kịch bản để duyệt và hỏi trước khi đăng.')
        if command == '/tin' and chat == self.owner:
            return self.jobs.put(('news_list', chat))  # Claude may write captions first: not on the polling thread
        link = URL.search(text)
        if link:
            self.jobs.put(('new', chat, link[0]))
            waiting = self.jobs.qsize() - 1
            self.say(chat, f'Đã nhận link, đang xếp hàng (trước còn {waiting} việc).' if waiting else 'Đã nhận link.')
        elif command:
            pass  # another bot's command in a group
        elif chat in self.current:
            self.jobs.put(('revise', chat, self.current[chat], text))
        elif message['chat']['type'] == 'private':
            self.say(chat, 'Gửi một link video để bắt đầu.')

    def ask_owner(self, chat, message):
        """A new chat sent /start: the first one becomes the owner, later ones wait for the owner's yes."""
        if self.owner is None:
            self.chats = [chat]; self.save_chats()
            return self.say(chat, 'Bạn là chủ bot. Người khác gửi /start sẽ cần bạn cho phép.\n\n' + HELP)
        if chat in self.asked:
            return self.say(chat, 'Đang chờ chủ bot cho phép.')
        self.asked.add(chat)
        buttons = [[{'text': '✅ Cho phép', 'callback_data': f'allow:{chat}'}, {'text': '❌ Từ chối', 'callback_data': f'deny:{chat}'}]]
        self.say(self.owner, f'{who(message)} muốn dùng bot (id {chat}).', reply_markup={'inline_keyboard': buttons})
        self.say(chat, 'Đã gửi yêu cầu tới chủ bot, chờ cho phép nhé.')

    def on_button(self, query):
        self.call('answerCallbackQuery', callback_query_id=query['id'])
        chat = query['message']['chat']['id']
        action, arg = query['data'].split(':', 1)
        if chat not in self.chats or (action in ('allow', 'deny', 'tt', 'ttp', 'ttn', 'ttnp') and chat != self.owner):
            return  # the TikTok account is the owner's: nobody else may post to it
        # Drop the buttons so the same choice isn't made twice.
        self.call('editMessageReplyMarkup', chat_id=chat, message_id=query['message']['message_id'], reply_markup={'inline_keyboard': []})
        if action in ('allow', 'deny'):
            other = int(arg); self.asked.discard(other)
            if action == 'allow' and other not in self.chats:
                self.chats.append(other); self.save_chats()
                self.say(other, 'Chủ bot đã cho phép. ' + HELP)
            elif action == 'deny':
                self.say(other, 'Chủ bot không cho phép dùng bot này.')
            return self.say(chat, f"{'Đã cho phép' if action == 'allow' else 'Đã từ chối'} {other}.")
        if action in ('ttn', 'ttnp'):
            return self.jobs.put(('tiktok_news', chat, *arg.split('/'), action == 'ttnp'))
        wd = reup.REUP / arg
        if action == 'ok':
            self.jobs.put(('make', chat, wd))
        elif action == 'redo':
            self.jobs.put(('revise', chat, wd, 'Viết lại một bản khác hẳn: tiêu đề và cách dẫn khác.'))
        elif action == 'no':
            if self.current.get(chat) == wd:
                del self.current[chat]
            self.say(chat, 'Đã bỏ video này.')
        elif action in ('tt', 'ttp'):
            self.jobs.put(('tiktok', chat, wd, action == 'ttp'))

    def poll(self):
        offset = None
        while True:
            try:
                updates = self.call('getUpdates', wait=POLL + 10, timeout=POLL, offset=offset, allowed_updates=['message', 'callback_query'])
            except Exception as e:
                log('poll failed:', e); time.sleep(5); continue
            for update in updates:
                offset = update['update_id'] + 1
                try:
                    if 'message' in update:
                        self.on_message(update['message'])
                    elif 'callback_query' in update:
                        self.on_button(update['callback_query'])
                except Exception:
                    log(traceback.format_exc())

    # --- jobs (one at a time, whoever sent them) --------------------------------------------------

    def show_script(self, chat, wd, script):
        self.current[chat] = wd
        buttons = [[{'text': '✅ Làm video', 'callback_data': f'ok:{wd.name}'}, {'text': '🔁 Viết lại', 'callback_data': f'redo:{wd.name}'},
                    {'text': '❌ Bỏ', 'callback_data': f'no:{wd.name}'}]]
        self.say(chat, reup.script_text(script) + '\n\nNhắn yêu cầu nếu muốn sửa kịch bản.', reply_markup={'inline_keyboard': buttons})

    def new(self, chat, link):
        note = self.say(chat, '⏬ Đang tải video…')
        wd = reup.fetch(link)
        analysis = json.loads((wd / 'analysis.json').read_text())
        self.status(note, f'⏬ Đã tải: dùng {analysis["clip_end"]:.1f} giây đầu.\n✍️ Claude đang xem video và viết kịch bản (1–2 phút)…')
        script = reup.write_script(wd)
        if not reup.settings()['auto_reup']:
            return self.show_script(chat, wd, script)
        self.say(chat, '🤖 Tự động, không cần duyệt:\n\n' + reup.script_text(script))
        self.make(chat, wd, post=True)

    def revise(self, chat, wd, request):
        self.say(chat, '✍️ Claude đang sửa kịch bản…')
        self.show_script(chat, wd, reup.write_script(wd, request))

    def make(self, chat, wd, post=False):
        """Voice and render, then the video back to the chat. `post`: the owner's goes to TikTok with no button."""
        note = self.say(chat, '🎙️ Đang đọc giọng và render, khoảng 1–3 phút…')
        try:
            video = reup.produce(wd)
        except BlockingIOError:
            self.status(note, '⏳ Máy đang làm video điểm tin hằng ngày. Sẽ thử lại sau 1 phút.')
            threading.Timer(RETRY_BUSY, self.jobs.put, args=(('make', chat, wd, post),)).start()
            return
        if self.current.get(chat) == wd:
            del self.current[chat]
        self.status(note, f'✅ Xong: {video.name}')
        if post and chat == self.owner and reup.settings()['tiktok_account']:
            self.post_soon(chat, wd)  # before the slow upload to Telegram
            return self.send_video(chat, wd, video)
        self.send_video(chat, wd, video)
        self.offer_tiktok(chat, wd)

    def post_soon(self, chat, wd, now=None):
        """Queue the video for TikTok; the watcher posts it in its slot (see tiktok_post.book_reup)."""
        now = now or datetime.now()
        at = tiktok_post.book_reup(wd.name, chat, now)
        if at - now < timedelta(minutes=2):
            return self.say(chat, '📤 Video này sẽ đăng TikTok ngay.')
        when = f'{at:%H:%M}' + (' sáng mai' if at.date() > now.date() else '')
        self.say(chat, f'⏳ Video này sẽ tự đăng TikTok khoảng {when} (cách bài trước ít nhất {tiktok_post.REUP_GAP} phút; '
                       'nếu có video tin nóng đang chờ thì tin nóng đăng trước).')

    def post_reups(self, now=None):
        entry = tiktok_post.due_reup(now)
        if entry:
            self.jobs.put(('tiktok', entry['chat'], reup.REUP / entry['name'], False))

    def offer_tiktok(self, chat, wd, private_too=True):
        """Only the owner gets the buttons, and only once an account is set in config.json."""
        account = reup.settings()['tiktok_account']
        if chat != self.owner or not account:
            return
        buttons = [{'text': '📤 Đăng TikTok', 'callback_data': f'tt:{wd.name}'}]
        if private_too:
            buttons.append({'text': '🔒 Đăng riêng tư', 'callback_data': f'ttp:{wd.name}'})
        self.say(chat, f'Đăng video này lên TikTok "{account}"? Nút Đăng TikTok sẽ đăng công khai ngay.', reply_markup={'inline_keyboard': [buttons]})

    def tiktok(self, chat, wd, private):
        conf = reup.settings()
        note = self.say(chat, '📤 Đang đăng lên TikTok…')
        try:
            video_id = tiktok_post.post_reup(wd, reup.OUTPUT / f'{wd.name}.mp4', conf['tiktok_account'], private, conf['tiktok_ai_label'])
        except tiktok_post.PostError as e:
            self.status(note, f'⚠️ {e}')
            if e.retry:
                self.offer_tiktok(chat, wd)
            return
        self.status(note, f"✅ Đã đăng TikTok{' ở chế độ riêng tư' if private else ''} (mã video {video_id}).")
        if private:
            self.offer_tiktok(chat, wd, private_too=False)

    # --- news videos: the day's render is put to the owner, one video at a time ----------------------

    def news_list(self, chat):
        self.offer_news(again=True)

    def offer_news(self, again=False):
        """Put newly rendered news videos to the owner, or post the next one if automatic posting is on.
        `again` (/tin) repeats every one not yet posted."""
        conf, day = reup.settings(), date.today().isoformat()
        account = conf['tiktok_account']
        if self.owner and not again and conf['tiktok_auto'] and conf['tiktok_auto_from'] and day >= conf['tiktok_auto_from']:
            return self.auto_news(day, account)
        if not self.owner or not account:
            return again and self.say(self.owner, 'Chưa đặt tài khoản TikTok trong config.json.')
        if not again and not tiktok_post.render_finished(day):
            return
        with pipeline_lock() as free:
            if not free:
                return again and self.say(self.owner, 'Máy đang làm video, thử lại sau khi xong.')
            videos = tiktok_post.news_videos(day)
        waiting = [v for v in videos if not tiktok_post.seen(v, 'public') and (again or not tiktok_post.seen(v, 'offered'))]
        if again and not waiting:
            self.say(self.owner, 'Hôm nay không còn video tin tức nào chờ đăng.')
        for item in waiting:
            tiktok_post.prepare_caption(day, item['id'])
            fresh = next((v for v in tiktok_post.news_videos(day) if v['id'] == item['id']), item)
            self.offer_news_video(fresh, account)
            tiktok_post.mark_offered(fresh)

    def auto_news(self, day, account):
        """One step of the day's automatic posting; the watcher calls this every minute."""
        if not tiktok_post.render_finished(day):
            return
        if not account:
            if tiktok_post.warn_once(day, 'account'):
                self.say(self.owner, '⚠️ Video tin hôm nay đã xong nhưng chưa kết nối tài khoản TikTok nên chưa đăng được. Hãy kết nối trong Studio.')
            return
        with pipeline_lock() as free:
            if not free:
                return
            videos = tiktok_post.news_videos(day)
        tiktok_post.fix_order(day, videos)
        limit = tiktok_post.daily_limit(reup.settings()['tiktok_auto_from'], day)
        left = tiktok_post.leftovers(day, videos, limit=limit)
        if left and tiktok_post.warn_once(day, 'late'):
            full = limit is not None and tiktok_post.posts_today(day) >= limit
            reason = f'Đã đủ {limit} bài của hôm nay' if full else f'Đã qua {tiktok_post.STOP_HOUR} giờ'
            self.say(self.owner, f"🌙 {reason}, hôm nay không đăng: {', '.join(left)}.")
        item = tiktok_post.next_auto(day, videos, limit=limit)
        if not item:
            tiktok_post.prepare_next_caption(day, videos, limit)
        if item:
            self.jobs.put(('tiktok_news', self.owner, day, item['id'], item['key'], False, True))

    def auto_news_failed(self, note, day, pid, error):
        outcome = tiktok_post.auto_failed(day, pid, error)
        log(f'auto post failed ({outcome}):', pid, error)
        ahead = {'retry': 'Sẽ tự thử lại sau 30–45 phút.', 'skip': 'Đã thử đủ số lần nên bỏ video này, sau 30–45 phút đăng video kế tiếp.',
                 'check': 'Video này sẽ không tự đăng lại.', 'stop': 'Việc tự đăng đang chờ bạn xử lý lỗi này.'}[outcome]
        if outcome != 'stop' or tiktok_post.warn_once(day, 'stop'):
            self.status(note, f'⚠️ {pid}: {error}\n{ahead}')
        else:
            self.status(note, f'⚠️ {pid}: chưa đăng được, vẫn chờ bạn xử lý lỗi đã báo.')

    def offer_news_video(self, item, account, private_too=True):
        ref = f"{item['day']}/{item['id']}/{item['key']}"
        buttons = [{'text': '📤 Đăng TikTok', 'callback_data': f'ttn:{ref}'}]
        if private_too:
            buttons.append({'text': '🔒 Đăng riêng tư', 'callback_data': f'ttnp:{ref}'})
        text = f"Video tin {item['id']}, đăng lên TikTok \"{account}\":\n\n{item['caption']}"
        markup = {'inline_keyboard': [buttons]}
        if item['preview'].exists():
            try:
                with open(item['preview'], 'rb') as f:
                    return self.call('sendPhoto', chat_id=self.owner, files={'photo': (item['preview'].name, f, 'image/jpeg')},
                                     caption=text[:1024], reply_markup=markup)
            except (RuntimeError, requests.RequestException) as e:
                log('preview not sent:', e)
        self.say(self.owner, text, reply_markup=markup)

    def tiktok_news(self, chat, day, pid, key, private, auto=False):
        conf = reup.settings()
        if auto and (not conf['tiktok_auto'] or pid in tiktok_post.read_auto(day).get('skipped', [])):  # stopped after this post was picked
            return
        tiktok_post.prepare_caption(day, pid)
        with pipeline_lock() as free:  # a render under way would overwrite the file being uploaded
            if not free:
                if not auto:
                    self.say(chat, f'⏳ Máy đang làm video. Sẽ đăng {pid} sau.')
                threading.Timer(RETRY_BUSY, self.jobs.put, args=(('tiktok_news', chat, day, pid, key, private, auto),)).start()
                return
            item = next((v for v in tiktok_post.news_videos(day) if v['id'] == pid), None)
            if not item and auto:  # being rendered again, or no longer planned: the schedule picks it up next time
                return log('auto post skipped, no finished video:', pid)
            # A button stands for what the owner saw. The schedule takes the latest version.
            if not auto and (not item or item['key'] != key):
                return self.say(chat, f'⚠️ Video {pid} đã thay đổi từ lúc gửi nút này nên chưa đăng. Gõ /tin để xem bản mới.')
            note = self.say(chat, f"📤 {'Tự động đ' if auto else 'Đ'}ang đăng {pid} lên TikTok…")
            try:
                video_id = tiktok_post.post_news(item, conf['tiktok_account'], private, conf['tiktok_ai_label'])
            except tiktok_post.PostError as e:
                if auto:
                    return self.auto_news_failed(note, day, pid, e)
                self.status(note, f'⚠️ {pid}: {e}')
                if e.retry:
                    self.offer_news_video(item, conf['tiktok_account'])
                return
        ahead = ''
        if auto:
            left = tiktok_post.waiting_videos(day, tiktok_post.news_videos(day), tiktok_post.read_auto(day))
            next_at = tiktok_post.read_auto(day).get('next_at', '')
            limit = tiktok_post.daily_limit(conf['tiktok_auto_from'], day)
            count = tiktok_post.posts_today(day)
            done = f'{count}/{limit}' if limit is not None else str(count)
            ahead = f'\nĐã đủ {limit} bài của hôm nay.' if limit is not None and count >= limit else \
                f'\nBài {done} hôm nay, bài kế tiếp khoảng {next_at[11:16]}.' if left else '\nĐã đăng hết video hôm nay.'
        self.status(note, f"✅ Đã đăng {pid}{' ở chế độ riêng tư' if private else ''} (mã video {video_id}).{ahead}")
        if private:
            self.offer_news_video(item, conf['tiktok_account'], private_too=False)

    def machine_needed(self):
        conf, day = reup.settings(), date.today().isoformat()
        if tiktok_post.reups_waiting():
            return True
        if not (conf['tiktok_auto'] and conf['tiktok_account'] and conf['tiktok_auto_from'] and day >= conf['tiktok_auto_from']):
            return False
        return tiktok_post.machine_needed(day, tiktok_post.news_videos(day), tiktok_post.daily_limit(conf['tiktok_auto_from'], day))

    def watch_news(self):
        awake = False
        while True:
            time.sleep(NEWS_CHECK)
            try:
                self.offer_news()
                self.post_reups()
                needed = self.machine_needed()
                if needed != awake:
                    keep_awake(needed)
                    awake = needed
                    log('keeping the machine awake for today\'s posts' if needed else 'machine may sleep again')
            except Exception:
                log(traceback.format_exc())

    def send_video(self, chat, wd, video):
        script = json.loads((wd / 'script.json').read_text())
        video = reup.smaller_copy(video)  # over Telegram's 50 MB, a compressed copy goes instead
        # Uploads from a home line can be slow (about 170 KB/s here) and drop: allow for 30 KB/s, try three times.
        wait = 120 + video.stat().st_size // 30_000
        for attempt in range(3):
            try:
                with open(video, 'rb') as f:
                    return self.call('sendVideo', chat_id=chat, files={'video': (video.name, f, 'video/mp4')}, wait=wait,
                                     caption=(script.get('caption') or ' '.join(script['title']))[:1024], supports_streaming=True, width=1080, height=1920)
            except requests.RequestException as e:
                log(f'sendVideo attempt {attempt + 1} failed:', e)
        raise RuntimeError(f'Không gửi được video qua Telegram (mạng chậm hoặc bị ngắt). File nằm trên máy: {video}')

    def work(self):
        while True:
            job = self.jobs.get()
            log('job', *job[:3])
            try:
                getattr(self, job[0])(*job[1:])
            except Exception as e:
                log(traceback.format_exc())
                try:
                    self.say(job[1], f'⚠️ Lỗi ({job[0]}): {str(e)[-800:]}')
                except Exception:
                    log('could not report the error')


def set_auto_reup(on):
    """Save the "auto_reup" switch in config.json."""
    config_file = reup.demo / 'config.json'
    config = json.loads(config_file.read_text(encoding='utf8')) if config_file.exists() else {}
    config.setdefault('reup', {})['auto_reup'] = bool(on)
    temporary = config_file.with_suffix('.tmp')
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    temporary.replace(config_file)


def single_instance():
    """Two bots polling one token make Telegram reject both; keep a lock file open for the process's life."""
    WORK.mkdir(parents=True, exist_ok=True)
    handle = open(WORK / 'telegram-bot.lock', 'w')
    try:
        if sys.platform == 'win32':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit('Bot đang chạy ở một tiến trình khác')
    return handle


def main():
    env = reup.dotenv()
    if not env.get('TELEGRAM_BOT_TOKEN'):
        sys.exit('Thiếu TELEGRAM_BOT_TOKEN trong .env')
    lock = single_instance()
    bot = Bot(env['TELEGRAM_BOT_TOKEN'], env.get('TELEGRAM_CHAT_ID', ''))
    me = bot.call('getMe')
    log(f"@{me['username']} đang chạy; được dùng: {bot.chats or 'chưa có ai (gửi /start)'}")
    threading.Thread(target=bot.work, daemon=True).start()
    threading.Thread(target=bot.watch_news, daemon=True).start()
    bot.poll()


if __name__ == '__main__':
    main()

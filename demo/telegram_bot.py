"""Telegram bot for demo/reup.py: send a video link, approve or revise Claude's script, get the MP4 back.

  .venv-demo/Scripts/python.exe demo/telegram_bot.py

Needs TELEGRAM_BOT_TOKEN in .env. Who may use it is TELEGRAM_CHAT_ID: chat ids separated by commas, the
first being the owner. The first chat that sends /start becomes the owner. Anyone else (another account,
or a group the bot was added to) who sends /start is put to the owner, who allows or refuses them with a
button. /ds lists the chats, /xoa <id> removes one.

Jobs run one at a time on this machine; renders wait for the daily videos (shared lock). Long polling:
the machine needs no open port.
"""
import json, queue, re, sys, threading, time, traceback
from datetime import datetime

import requests

import reup
from edition import WORK

URL = re.compile(r'https?://\S+')
POLL = 50  # seconds a getUpdates call waits for messages
RETRY_BUSY = 60  # seconds before a render tries the lock again
HELP = ('Gửi link video (X, TikTok, YouTube, Facebook…). Mình tải video, nhờ Claude viết kịch bản, '
        'gửi bạn duyệt rồi đọc giọng và render.\n\nKhi đang xem kịch bản, nhắn một câu bất kỳ để yêu cầu sửa, '
        'ví dụ: "ngắn lại", "tiêu đề giật hơn", "bỏ câu thứ ba".')


def log(*parts):
    print(datetime.now().strftime('%Y-%m-%d %H:%M:%S'), *parts, flush=True)


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
        if chat not in self.chats or (action in ('allow', 'deny') and chat != self.owner):
            return
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
        wd = reup.REUP / arg
        if action == 'ok':
            self.jobs.put(('make', chat, wd))
        elif action == 'redo':
            self.jobs.put(('revise', chat, wd, 'Viết lại một bản khác hẳn: tiêu đề và cách dẫn khác.'))
        elif action == 'no':
            if self.current.get(chat) == wd:
                del self.current[chat]
            self.say(chat, 'Đã bỏ video này.')

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
        self.show_script(chat, wd, reup.write_script(wd))

    def revise(self, chat, wd, request):
        self.say(chat, '✍️ Claude đang sửa kịch bản…')
        self.show_script(chat, wd, reup.write_script(wd, request))

    def make(self, chat, wd):
        note = self.say(chat, '🎙️ Đang đọc giọng và render, khoảng 1–3 phút…')
        try:
            video = reup.produce(wd)
        except BlockingIOError:
            self.status(note, '⏳ Máy đang làm video điểm tin hằng ngày. Sẽ thử lại sau 1 phút.')
            threading.Timer(RETRY_BUSY, self.jobs.put, args=(('make', chat, wd),)).start()
            return
        self.send_video(chat, wd, video)
        self.status(note, f'✅ Xong: {video.name}')
        if self.current.get(chat) == wd:
            del self.current[chat]

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
    bot.poll()


if __name__ == '__main__':
    main()

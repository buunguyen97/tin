"""Local web UI for the day's videos: plan from the sheet, preview, render, watch.

  .venv-demo/bin/python demo/studio.py      ->  http://127.0.0.1:8790
Listens on localhost only; it runs the same demo/run_daily.py as the command line.
"""
import json, mimetypes, os, re, socket, subprocess, sys, threading, webbrowser
from datetime import date as dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import reup
import tiktok_post
from edition import VOICES, WORK, load_edition, pipeline_lock
from run_daily import LABELS, STEPS

demo = Path(__file__).resolve().parent
project = demo.parent
daily = project / 'output' / 'daily'
PORT = int(os.environ.get('STUDIO_PORT', 8790))
DATE = re.compile(r'\d{4}-\d{2}-\d{2}')
ID = re.compile(r'[a-z0-9-]+')
VIDEO_ID = re.compile(r'[A-Za-z0-9_-]+')
ACCOUNT = re.compile(r'[A-Za-z0-9_.-]{1,128}')  # what autotok accepts as an account name
ANSI = re.compile(r'\x1b\[[0-9;]*m')  # Remotion colours its console output
# URL prefix -> (folder, allowed suffixes, allowed sub-folder depth)
SERVED = {'assets': (demo / 'assets', {'.jpg', '.png', '.webp', '.svg'}, 0),
          'output': (daily, {'.mp4', '.jpg', '.png', '.md', '.txt'}, 1),
          'work': (WORK, {'.jpg', '.svg'}, 2),
          'reup': (tiktok_post.REUP_OUTPUT, {'.mp4'}, 0)}


class Job:
    """At most one pipeline run at a time; its output is kept for the page to poll."""
    lock = threading.Lock()
    state = {'running': False, 'log': [], 'code': None, 'steps': [], 'date': None, 'programmes': []}

    @classmethod
    def start(cls, date, voice, steps, programmes, csv=None):
        with cls.lock:
            if cls.state['running']:
                return False
            cls.state = {'running': True, 'log': [], 'code': None, 'steps': steps, 'date': date, 'programmes': programmes}
        cmd = [sys.executable, str(demo / 'run_daily.py'), '--date', date, '--voice', voice, '--steps', ','.join(steps)]
        if programmes:
            cmd += ['--programmes', ','.join(programmes)]
        if csv:
            cmd += ['--csv', csv]
        threading.Thread(target=cls._run, args=(cmd,), daemon=True).start()
        return True

    @classmethod
    def _run(cls, cmd):
        env = {**os.environ, 'PYTHONUNBUFFERED': '1'}
        try:
            proc = subprocess.Popen(cmd, cwd=project, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding='utf-8', errors='replace', bufsize=1)
            for line in proc.stdout:
                cls.state['log'].append(ANSI.sub('', line.rstrip('\n')))
            code = proc.wait()
        except OSError as e:
            cls.state['log'].append(f'LỖI: {e}')
            code = 1
        cls.state.update(running=False, code=code)


def days():
    found = {p.name for base in (daily, WORK) if base.exists() for p in base.iterdir() if p.is_dir() and DATE.fullmatch(p.name)}
    return sorted(found, reverse=True)


def stamp(path):
    return f'?v={int(path.stat().st_mtime)}'


def day_view(date):
    plan_file = WORK / date / 'plan.json'
    if not plan_file.exists():
        return None
    plan = json.loads(plan_file.read_text())
    for prog in plan['programmes']:
        wd, video = WORK / date / prog['id'], daily / date / f'{prog["id"]}.mp4'
        if video.exists():
            prog['video'] = {'url': f'/output/{date}/{video.name}{stamp(video)}', 'mb': round(video.stat().st_size / 1e6, 1),
                             'download': f'/output/{date}/{video.name}', 'contact': f'/output/{date}/{video.stem}-contact-sheet.jpg',
                             'sources': f'/output/{date}/{video.stem}-nguon.md'}
        if prog['status'] != 'ready' or not (wd / 'news.json').exists():
            continue
        edition = load_edition(wd / 'news.json')
        images = json.loads((wd / 'images.json').read_text()) if (wd / 'images.json').exists() else {}
        prog['accent'] = edition.get('intro_accent')
        tts = wd / 'tts-vieneu.json'  # the voice actually recorded, which may be the Studio default
        prog['voice'] = json.loads(tts.read_text())['voice'] if tts.exists() else edition.get('voice')
        if (wd / 'intro-frame.jpg').exists() and (wd / 'intro.svg').exists():
            prog['intro'] = {'frame': f'/work/{date}/{prog["id"]}/intro-frame.jpg{stamp(wd / "intro-frame.jpg")}',
                             'card': f'/work/{date}/{prog["id"]}/intro.svg{stamp(wd / "intro.svg")}'}
        prog['items'] = []
        for i, s in enumerate(edition['stories'], 1):
            entry = images.get(str(i))
            prog['items'].append({'title': ' '.join(s['title']), 'number': s['number'], 'unit': s['unit'],
                                  'category': s['category'], 'accent': s['accent'], 'url': s['url'], 'source': s['source'],
                                  'script': s['script'], 'image': f'/assets/{entry["file"]}' if entry else None,
                                  'no_image': s.get('image') is False})
    return plan


def config_view():
    path = Path(os.environ.get('DIEMTIN_CONFIG', demo / 'config.json'))
    if not path.exists():
        return {'ok': False, 'message': 'Chưa có demo/config.json'}
    config = json.loads(path.read_text())
    sheet = config.get('sheet_id', '')
    ok = bool(sheet) and 'DÁN' not in sheet
    mode = 'service account' if config.get('service_account_file') else 'link chia sẻ'
    return {'ok': ok, 'message': f'Sheet …{sheet[-6:]} qua {mode}' if ok else 'Chưa điền sheet_id trong demo/config.json',
            'url': f'https://docs.google.com/spreadsheets/d/{sheet}/edit' if ok else None,
            'voice': config.get('voice', VOICES[0][0])}


class TikTok:
    """One login and one post at a time, run in the background; the page polls for the outcome."""
    lock = threading.Lock()
    post = {'running': False, 'label': '', 'message': '', 'ok': None}
    login = {'running': False, 'label': '', 'message': '', 'ok': None}

    @classmethod
    def start(cls, slot, label, work):
        with cls.lock:
            if getattr(cls, slot)['running']:
                return False
            setattr(cls, slot, {'running': True, 'label': label, 'message': '', 'ok': None})
        threading.Thread(target=cls._run, args=(slot, work), daemon=True).start()
        return True

    @classmethod
    def _run(cls, slot, work):
        try:
            ok, message = True, work()
        except Exception as e:  # PostError, or anything else: the page must not wait forever
            ok, message = False, str(e)
        getattr(cls, slot).update(running=False, ok=ok, message=message)


def find_video(kind, day, pid):
    videos = tiktok_post.news_videos(day) if kind == 'news' else tiktok_post.reup_videos(limit=None)
    return next((v for v in videos if v['id'] == pid), None)


def video_view(item):
    folder = f"/output/{item['day']}" if item['kind'] == 'news' else '/reup'
    return {'kind': item['kind'], 'id': item['id'], 'day': item.get('day'), 'title': item['title'], 'caption': item['caption'], 'key': item['key'],
            'url': f"{folder}/{item['video'].name}{stamp(item['video'])}", 'status': tiktok_post.status(item)}


channels = {}  # account -> channel name, asked of TikTok once


def channel_view(account):
    if account not in channels:
        channels[account] = tiktok_post.channel_name(account)
    return channels[account]


def tiktok_view(date):
    conf, known = reup.settings(), tiktok_post.accounts()
    if known and conf['tiktok_account'] not in known:  # logged in from the command line, or the saved account is gone
        conf['tiktok_account'] = known[0]
        tiktok_post.save_settings(known[0], conf['tiktok_ai_label'], conf['tiktok_auto'])
    today = dt.today().isoformat()
    limit = tiktok_post.daily_limit(conf['tiktok_auto_from'], max(today, conf['tiktok_auto_from'])) if conf['tiktok_auto_from'] else None
    return {'installed': known is not None, 'account': conf['tiktok_account'] if known else '', 'ai_label': conf['tiktok_ai_label'], 'auto': conf['tiktok_auto'],
            'auto_from': conf['tiktok_auto_from'], 'today': today, 'gap': tiktok_post.GAP_MINUTES, 'stop_hour': tiktok_post.STOP_HOUR,
            'limit': limit, 'schedule': tiktok_post.auto_view(today, tiktok_post.news_videos(today), limit), 'post': TikTok.post, 'login': TikTok.login, 'days': days(),
            'videos': [video_view(v) for v in (tiktok_post.news_videos(date) if DATE.fullmatch(date) else [])],
            'reup_videos': [video_view(v) for v in tiktok_post.reup_videos()]}


def connect_account(name, again):
    """Opens a browser window for the owner to log in; autotok keeps the session in ~/.autotok."""
    def work():
        cmd = [sys.executable, '-m', 'autotok', 'login', '-n', name] + (['--force'] if again else [])
        done = subprocess.run(cmd, cwd=project, capture_output=True, text=True, encoding='utf-8', errors='replace')
        if done.returncode:
            raise tiktok_post.PostError((done.stderr or done.stdout).strip()[-300:] or 'Không đăng nhập được.')
        conf = reup.settings()
        tiktok_post.save_settings(name, conf['tiktok_ai_label'], conf['tiktok_auto'])
        channels.pop(name, None)  # logged in again, perhaps as another channel
        return 'Đã kết nối tài khoản TikTok.'
    return work


def post_video(kind, day, pid, key, private, text):
    conf = reup.settings()
    account = conf['tiktok_account']

    def send():
        item = find_video(kind, day, pid)
        if not item or item['key'] != key:  # what is on the page is no longer what would go out
            raise tiktok_post.PostError('Video đã thay đổi từ lúc mở trang nên chưa đăng. Hãy tải lại trang.')
        video_id = tiktok_post.post_item(item, account, private, conf['tiktok_ai_label'], text)
        return f"Đã đăng {pid}{' ở chế độ riêng tư' if private else ' công khai'} (mã video {video_id})."

    def work():
        if kind != 'news':
            return send()
        with pipeline_lock() as free:  # a render under way would overwrite the file being uploaded
            if not free:
                raise tiktok_post.PostError('Máy đang làm video. Hãy đăng lại khi máy làm xong.')
            return send()
    return work


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def trusted(self):
        """Refuse requests sent by other web pages: posting to TikTok must only come from this page."""
        hosts = {f'127.0.0.1:{PORT}', f'localhost:{PORT}'}
        origin = self.headers.get('Origin')
        return self.headers.get('Host') in hosts and (origin is None or urlparse(origin).netloc in hosts)

    def tiktok_action(self, path, body):
        action = path.rsplit('/', 1)[-1]
        known = tiktok_post.accounts() or []
        if action in ('login', 'check', 'remove'):
            name = str(body.get('name', ''))
            if not ACCOUNT.fullmatch(name):
                return self.send_json({'error': 'Tên tài khoản chỉ gồm chữ không dấu, số, dấu chấm, gạch dưới hoặc gạch ngang.'}, 400)
            if action == 'login':
                if tiktok_post.accounts() is None:
                    return self.send_json({'error': 'Chưa cài autotok.'}, 400)
                if not TikTok.start('login', name, connect_account(name, name in known)):
                    return self.send_json({'error': 'Đang có một lượt đăng nhập khác.'}, 409)
                return self.send_json({'ok': True})
            if name not in known:
                return self.send_json({'error': 'Không có tài khoản này.'}, 404)
            import autotok
            if action == 'remove':
                autotok.AccountStore().delete(name)
                channels.pop(name, None)
                conf = reup.settings()
                tiktok_post.save_settings('', conf['tiktok_ai_label'], False)  # no account, nothing to post to
                return self.send_json({'ok': True})
            try:
                return self.send_json({'ok': autotok.Client.from_account(name).check_session()})
            except Exception as e:
                return self.send_json({'ok': False, 'message': str(e)})
        if action == 'settings':
            tiktok_post.save_settings(reup.settings()['tiktok_account'], body.get('ai_label'), body.get('auto'))
            return self.send_json({'ok': True})
        if action not in ('post', 'forget', 'caption', 'skip'):
            return self.send_error(404)
        kind, day, pid, key = body.get('kind'), str(body.get('day') or ''), str(body.get('id', '')), str(body.get('key', ''))
        if kind not in ('news', 'reup') or not VIDEO_ID.fullmatch(pid) or (kind == 'news' and not DATE.fullmatch(day)):
            return self.send_json({'error': 'Video không hợp lệ.'}, 400)
        if action == 'skip':
            if kind != 'news':
                return self.send_json({'error': 'Video reup không tự đăng.'}, 400)
            tiktok_post.set_skipped(day, pid, bool(body.get('skip')))
            return self.send_json({'ok': True})
        if action == 'caption':
            text = str(body.get('caption', '')).strip()
            if not text or len(text) > tiktok_post.CAPTION_LIMIT:
                return self.send_json({'error': f'Caption phải có từ 1 đến {tiktok_post.CAPTION_LIMIT} ký tự.'}, 400)
            item = find_video(kind, day, pid)
            if not item or item['key'] != key:
                return self.send_json({'error': 'Video đã thay đổi. Hãy tải lại trang.'}, 409)
            tiktok_post.save_caption(item, text)
            return self.send_json({'ok': True})
        if action == 'forget':
            item = find_video(kind, day, pid)
            if not item or item['key'] != key:
                return self.send_json({'error': 'Video đã thay đổi. Hãy tải lại trang.'}, 409)
            tiktok_post.forget_uncertain(item)
            return self.send_json({'ok': True})
        if not reup.settings()['tiktok_account']:
            return self.send_json({'error': 'Chưa kết nối tài khoản TikTok.'}, 400)
        text = str(body.get('caption', '')).strip()
        if not text or len(text) > tiktok_post.CAPTION_LIMIT:
            return self.send_json({'error': f'Caption phải có từ 1 đến {tiktok_post.CAPTION_LIMIT} ký tự.'}, 400)
        if not TikTok.start('post', pid, post_video(kind, day, pid, key, bool(body.get('private')), text)):
            return self.send_json({'error': 'Đang đăng một video khác, chờ xong rồi bấm lại.'}, 409)
        self.send_json({'ok': True})

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        path = unquote(url.path)
        if path == '/':
            return self.send_file(demo / 'studio.html')
        if path == '/api/state':
            date = parse_qs(url.query).get('date', [''])[0]
            return self.send_json({'config': config_view(), 'voices': VOICES, 'labels': LABELS, 'job': Job.state,
                                   'days': days(), 'day': day_view(date) if DATE.fullmatch(date) else None})
        if path == '/api/tiktok':
            return self.send_json(tiktok_view(parse_qs(url.query).get('date', [''])[0]))
        if path == '/api/tiktok/channel':
            account = reup.settings()['tiktok_account']
            return self.send_json({'channel': channel_view(account) if account in (tiktok_post.accounts() or []) else None})
        prefix, _, rest = path.strip('/').partition('/')
        if prefix in SERVED and rest:
            base, allowed, depth = SERVED[prefix]
            target = (base / rest).resolve()
            inside = target.is_relative_to(base.resolve()) and len(target.relative_to(base.resolve()).parts) <= depth + 1
            if inside and target.suffix in allowed and target.is_file():
                return self.send_file(target)
        self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path != '/api/run' and not path.startswith('/api/tiktok/'):
            return self.send_error(404)
        if not self.trusted():
            return self.send_json({'error': 'Yêu cầu không đến từ Studio'}, 403)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
        except ValueError:
            return self.send_json({'error': 'JSON không hợp lệ'}, 400)
        if path != '/api/run':
            return self.tiktok_action(path, body)
        date, voice = str(body.get('date', '')), body.get('voice', VOICES[0][0])
        steps = [s for s in STEPS if s in body.get('steps', STEPS)]
        programmes = [p for p in body.get('programmes') or [] if ID.fullmatch(str(p))]
        csv = body.get('csv') or None
        if not DATE.fullmatch(date) or voice not in [v for v, _ in VOICES] or not steps:
            return self.send_json({'error': 'Ngày, giọng đọc hoặc bước không hợp lệ'}, 400)
        if csv and not (demo / Path(csv).name).is_file():
            return self.send_json({'error': 'Không thấy file CSV trong demo/'}, 400)
        if not Job.start(date, voice, steps, programmes, str(demo / Path(csv).name) if csv else None):
            return self.send_json({'error': 'Đang có một lượt chạy khác'}, 409)
        self.send_json({'ok': True})

    def send_file(self, path):
        size = path.stat().st_size
        ctype = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        if ctype.startswith('text/') or ctype.endswith('json'):
            ctype += '; charset=utf-8'
        start, end = 0, size - 1
        m = re.fullmatch(r'bytes=(\d*)-(\d*)', self.headers.get('Range', ''))
        if m and (m[1] or m[2]):  # <video> seeks with byte ranges
            start, end = (int(m[1]), int(m[2]) if m[2] else size - 1) if m[1] else (size - int(m[2]), size - 1)
            end = min(end, size - 1)
            self.send_response(206)
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        else:
            self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(end - start + 1))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        with open(path, 'rb') as f:
            f.seek(start)
            remaining = end - start + 1
            try:
                while remaining > 0:
                    chunk = f.read(min(1 << 16, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass


if __name__ == '__main__':
    daily.mkdir(parents=True, exist_ok=True)
    address = f'http://127.0.0.1:{PORT}'
    try:  # Windows lets a second server bind the same port, so look for the first one rather than wait for an error
        socket.create_connection(('127.0.0.1', PORT), timeout=1).close()
        running = True
    except OSError:
        running = False
    server = None if running else ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    if '--open' in sys.argv:
        webbrowser.open(address + '/#tiktok')
    if server:
        print(f'Studio: {address}', flush=True)
        server.serve_forever()
    else:
        print(f'Studio đang chạy: {address}', flush=True)

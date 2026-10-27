"""Local web UI for the day's videos: plan from the sheet, preview, render, watch.

  .venv-demo/bin/python demo/studio.py      ->  http://127.0.0.1:8790
Listens on localhost only; it runs the same demo/run_daily.py as the command line.
"""
import json, mimetypes, os, re, subprocess, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from edition import VOICES, WORK, load_edition
from run_daily import LABELS, STEPS

demo = Path(__file__).resolve().parent
project = demo.parent
daily = project / 'output' / 'daily'
PORT = int(os.environ.get('STUDIO_PORT', 8790))
DATE = re.compile(r'\d{4}-\d{2}-\d{2}')
ID = re.compile(r'[a-z0-9-]+')
ANSI = re.compile(r'\x1b\[[0-9;]*m')  # Remotion colours its console output
# URL prefix -> (folder, allowed suffixes, allowed sub-folder depth)
SERVED = {'assets': (demo / 'assets', {'.jpg', '.png', '.webp', '.svg'}, 0),
          'output': (daily, {'.mp4', '.jpg', '.png', '.md', '.txt'}, 1),
          'work': (WORK, {'.jpg', '.svg'}, 2)}


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
                                    text=True, bufsize=1)
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
            entry = images.get(str(i)) if s.get('image') is not False else None
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


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

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
        prefix, _, rest = path.strip('/').partition('/')
        if prefix in SERVED and rest:
            base, allowed, depth = SERVED[prefix]
            target = (base / rest).resolve()
            inside = target.is_relative_to(base.resolve()) and len(target.relative_to(base.resolve()).parts) <= depth + 1
            if inside and target.suffix in allowed and target.is_file():
                return self.send_file(target)
        self.send_error(404)

    def do_POST(self):
        if urlparse(self.path).path != '/api/run':
            return self.send_error(404)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
        except ValueError:
            return self.send_json({'error': 'JSON không hợp lệ'}, 400)
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
    print(f'Studio: http://127.0.0.1:{PORT}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()

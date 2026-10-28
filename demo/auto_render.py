"""Render today's videos automatically once the Google Sheet has approved stories.

Meant to run every few minutes (launchd, see demo/launchd/). Each run:
  1. exits unless the local time is inside config "auto_render.window" (default 7:00–12:00)
  2. reads the sheet and fingerprints today's approved stories
  3. if the fingerprint differs from the last render, runs run_daily.py for today
     (a failing sheet is retried at most "auto_render.max_attempts" times, default 2)
  4. posts a macOS notification with the result, or once near the end of the window if there is no news
  5. after a successful render, deletes videos, work folders and cached media older than "retention_days"

  .venv-demo/bin/python demo/auto_render.py            # one check, as launchd does
  .venv-demo/bin/python demo/auto_render.py --force    # ignore time window, fingerprint and attempts
"""
import argparse, hashlib, json, re, shutil, subprocess, sys, time
from datetime import date as Date, datetime, timedelta
from pathlib import Path

from edition import BUSY, WORK
from sheet_to_news import SheetError, approved_stories, fetch_rows, load_config, rows_to_dicts

demo = Path(__file__).resolve().parent
project = demo.parent
DAILY = project / 'output' / 'daily'
ASSETS = demo / 'assets'
CACHED = ['img-*', 'vieneu-*.wav', 'edge-*.mp3']  # content-addressed, re-created on demand
DAY_DIR = re.compile(r'\d{4}-\d{2}-\d{2}')


def log(*parts):
    print(datetime.now().strftime('%Y-%m-%d %H:%M:%S'), *parts, flush=True)


def notify(title, message):
    script = f'display notification {json.dumps(message)} with title {json.dumps(title)}'
    subprocess.run(['osascript', '-e', script], capture_output=True)


def fingerprint(stories):
    payload = json.dumps([(s['sector'], s['hot'], s['hot_order'], s['order'], s['story']) for s in stories],
                         ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def should_render(state, current, max_attempts):
    """Render when the sheet changed; retry a failing sheet only `max_attempts` times."""
    if state.get('fingerprint') != current:
        return True
    return not state.get('ok', True) and state.get('attempts', 0) < max_attempts


def cleanup(today, keep_days, now=None):
    """Delete day folders older than `keep_days` and cached media unused for as long."""
    oldest = today - timedelta(days=keep_days)
    removed = []
    for base in (DAILY, WORK):
        for d in base.glob('*') if base.exists() else []:
            if d.is_dir() and DAY_DIR.fullmatch(d.name) and Date.fromisoformat(d.name) < oldest:
                shutil.rmtree(d)
                removed.append(d.name)
    limit = (now or time.time()) - keep_days * 86400
    for pattern in CACHED:  # prepare.py and fetch_images.py touch a file whenever they reuse it
        for f in ASSETS.glob(pattern):
            if f.stat().st_mtime < limit:
                f.unlink()
                removed.append(f.name)
    return removed


def ready_videos(day):
    plan = WORK / day.isoformat() / 'plan.json'
    if not plan.exists():
        return []
    return [p['id'] for p in json.loads(plan.read_text())['programmes'] if p['status'] == 'ready']


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args(argv)
    now = datetime.now()
    try:
        config = load_config()
    except SheetError as e:
        log('LỖI:', e)
        return 1
    settings = config.get('auto_render', {})
    start, end = settings.get('window', [7, 12])
    if not args.force and not start <= now.hour < end:
        return 0
    day = now.date()
    state_file = WORK / day.isoformat() / 'auto-render.json'
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    try:
        stories, _, errors = approved_stories(rows_to_dicts(fetch_rows(config)), day, config.get('freshness'))
    except SheetError as e:
        log('LỖI đọc Sheet:', e)
        return 1
    if not stories:
        log(f'Chưa có tin đã duyệt ngày {day:%d/%m/%Y}' + (f' ({len(errors)} tin bị loại)' if errors else ''))
        if now.hour == end - 1 and not state.get('no_news_notified'):
            notify('Điểm tin – chưa có tin', f'Đến {now:%H:%M} Sheet vẫn chưa có tin hợp lệ ngày {day:%d/%m}'
                   + (f' ({len(errors)} tin bị loại, xem output/auto-render.log)' if errors else '; kiểm tra routine Claude'))
            for e in errors:
                log('  LOẠI:', e)
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.write_text(json.dumps({**state, 'no_news_notified': True}, ensure_ascii=False))
        return 0
    current = fingerprint(stories)
    max_attempts = settings.get('max_attempts', 2)
    if not args.force and not should_render(state, current, max_attempts):
        return 0
    log(f'{len(stories)} tin đã duyệt, Sheet đã thay đổi -> tạo video')
    for e in errors:
        log('  LOẠI:', e)
    proc = subprocess.run([sys.executable, str(demo / 'run_daily.py'), '--date', day.isoformat()],
                          cwd=project, capture_output=True, text=True)
    sys.stdout.write(proc.stdout[-4000:])
    if proc.returncode == BUSY:  # Studio is making videos; look again at the next check
        log('Đang có lượt tạo video khác, kiểm tra lại lần sau')
        return 0
    attempts = state.get('attempts', 0) + 1 if state.get('fingerprint') == current else 1
    videos = ready_videos(day)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({'fingerprint': current, 'ok': proc.returncode == 0, 'attempts': attempts,
                                      'rendered_at': now.isoformat(), 'stories': len(stories), 'dropped': len(errors),
                                      'videos': videos}, ensure_ascii=False))
    dropped = f'; loại {len(errors)} tin cũ/trùng' if errors else ''
    if proc.returncode == 0:
        notify('Điểm tin', f'Đã tạo {len(videos)} video ngày {day:%d/%m}{dropped}: ' + ', '.join(videos))
        log('XONG', videos)
        removed = cleanup(day, config.get('retention_days', 14))
        if removed:
            log(f'Dọn {len(removed)} thư mục/tệp cũ hơn {config.get("retention_days", 14)} ngày')
        return 0
    retry = 'sẽ thử lại lần sau' if attempts < max_attempts else 'đã dừng thử lại tới khi Sheet thay đổi'
    notify('Điểm tin – lỗi', f'Tạo video ngày {day:%d/%m} thất bại ({retry}), xem output/auto-render.log')
    log('THẤT BẠI', proc.returncode, f'lần {attempts}/{max_attempts}', proc.stderr[-2000:])
    return 1


if __name__ == '__main__':
    sys.exit(main())

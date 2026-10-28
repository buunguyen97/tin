"""Render today's videos automatically once the Google Sheet has approved stories.

Meant to run every few minutes (launchd, see demo/launchd/). Each run:
  1. exits unless the local time is inside config "auto_render.window" (default 7:00–12:00)
  2. reads the sheet and fingerprints today's approved stories
  3. if the fingerprint differs from the last successful render, runs run_daily.py for today
  4. posts a macOS notification with the result

  .venv-demo/bin/python demo/auto_render.py            # one check, as launchd does
  .venv-demo/bin/python demo/auto_render.py --force    # ignore time window and fingerprint
"""
import argparse, fcntl, hashlib, json, os, subprocess, sys
from datetime import datetime
from pathlib import Path

from edition import WORK
from sheet_to_news import SheetError, approved_stories, fetch_rows, load_config, rows_to_dicts

demo = Path(__file__).resolve().parent
project = demo.parent
LOCK = WORK / 'auto-render.lock'


def log(*parts):
    print(datetime.now().strftime('%Y-%m-%d %H:%M:%S'), *parts, flush=True)


def notify(title, message):
    script = f'display notification {json.dumps(message)} with title {json.dumps(title)}'
    subprocess.run(['osascript', '-e', script], capture_output=True)


def fingerprint(stories):
    payload = json.dumps([(s['sector'], s['hot'], s['hot_order'], s['order'], s['story']) for s in stories],
                         ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


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
    start, end = config.get('auto_render', {}).get('window', [7, 12])
    if not args.force and not start <= now.hour < end:
        return 0
    day = now.date()
    state_file = WORK / day.isoformat() / 'auto-render.json'
    WORK.mkdir(parents=True, exist_ok=True)
    with open(LOCK, 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log('Lượt trước còn đang chạy, bỏ qua')
            return 0
        try:
            stories, _, _ = approved_stories(rows_to_dicts(fetch_rows(config)), day)
        except SheetError as e:
            log('LỖI đọc Sheet:', e)
            return 1
        if not stories:
            log(f'Chưa có tin đã duyệt ngày {day:%d/%m/%Y}')
            return 0
        current = fingerprint(stories)
        previous = json.loads(state_file.read_text()).get('fingerprint') if state_file.exists() else None
        if current == previous and not args.force:
            return 0
        log(f'{len(stories)} tin đã duyệt, Sheet đã thay đổi -> tạo video')
        cmd = [sys.executable, str(demo / 'run_daily.py'), '--date', day.isoformat()]
        proc = subprocess.run(cmd, cwd=project, capture_output=True, text=True)
        sys.stdout.write(proc.stdout[-4000:])
        out_dir = project / 'output' / 'daily' / day.isoformat()
        videos = sorted(p.stem for p in out_dir.glob('*.mp4')) if out_dir.exists() else []
        if proc.returncode == 0:
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.write_text(json.dumps({'fingerprint': current, 'rendered_at': now.isoformat(),
                                              'stories': len(stories), 'videos': videos}, ensure_ascii=False))
            notify('Điểm tin', f'Đã tạo {len(videos)} video ngày {day:%d/%m}: ' + ', '.join(videos))
            log('XONG', videos)
            return 0
        # Leave the fingerprint unchanged so the next check retries.
        notify('Điểm tin – lỗi', f'Tạo video ngày {day:%d/%m} thất bại, xem output/auto-render.log')
        log('THẤT BẠI', proc.returncode, proc.stderr[-2000:])
        return 1


if __name__ == '__main__':
    sys.exit(main())

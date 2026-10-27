"""Make the day's videos: Google Sheet -> plan -> images -> voice -> render -> QA.

One video per sector with enough approved stories, plus the round-up of hot stories.
  .venv-demo/bin/python demo/run_daily.py                          # today, every programme
  .venv-demo/bin/python demo/run_daily.py --date 2026-10-27 --csv demo/sheet-template.csv
  .venv-demo/bin/python demo/run_daily.py --steps sheet,images     # plan and preview only
  .venv-demo/bin/python demo/run_daily.py --programmes tong-hop    # remake one video
"""
import argparse, json, os, shutil, subprocess, sys, time
from datetime import date as Date
from pathlib import Path

from edition import VOICES, WORK

demo = Path(__file__).resolve().parent
project = demo.parent
STEPS = ['sheet', 'images', 'prepare', 'render', 'verify']
LABELS = {'sheet': 'Đọc Google Sheet, chia tin theo video', 'images': 'Tải ảnh bài báo',
          'prepare': 'Đọc giọng + dựng intro, thẻ tin', 'render': 'Render video', 'verify': 'Kiểm tra video'}


def vieneu_python():
    path = Path(os.environ.get('VIENEU_PYTHON', project.parent / 'VieNeu-TTS' / '.venv' / 'bin' / 'python'))
    if not path.exists():
        sys.exit(f'Không thấy Python của VieNeu tại {path}; đặt biến VIENEU_PYTHON')
    return str(path)


def day_output(day):
    return project / 'output' / 'daily' / day


def run(cmd):
    print('$', ' '.join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], cwd=project, check=True)


def ready_programmes(day, only):
    plan_file = WORK / day / 'plan.json'
    if not plan_file.exists():
        sys.exit(f'Chưa có kế hoạch cho ngày {day}; chạy bước sheet trước')
    ready = [p for p in json.loads(plan_file.read_text())['programmes'] if p['status'] == 'ready']
    if only:
        ready = [p for p in ready if p['id'] in only]
    if not ready:
        sys.exit('Không có video nào để làm')
    return ready


def main(argv=None):
    config_file = Path(os.environ.get('DIEMTIN_CONFIG', demo / 'config.json'))
    config = json.loads(config_file.read_text()) if config_file.exists() else {}
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--date', default=Date.today().isoformat(), help='YYYY-MM-DD (mặc định: hôm nay)')
    parser.add_argument('--voice', default=config.get('voice', 'Hải Đăng'),
                        help='Giọng mặc định (chương trình có "voice" riêng thì dùng giọng đó)')
    parser.add_argument('--csv', help='Đọc tin từ CSV thay vì Google Sheet')
    parser.add_argument('--steps', default=','.join(STEPS), help='Các bước: ' + ','.join(STEPS))
    parser.add_argument('--programmes', default='', help='Chỉ làm các video này (id, cách nhau bởi dấu phẩy)')
    args = parser.parse_args(argv)
    steps = [s for s in STEPS if s in args.steps.split(',')]
    if not steps or set(args.steps.split(',')) - set(STEPS):
        parser.error('Bước không hợp lệ')
    if args.voice not in [v for v, _ in VOICES]:
        parser.error(f'Giọng không có: {args.voice}')
    only = {p for p in args.programmes.split(',') if p}

    py, out_dir, started, failed = sys.executable, day_output(args.date), time.monotonic(), []
    for n, step in enumerate(steps, 1):
        print(f'\n== BƯỚC {n}/{len(steps)}: {LABELS[step]}', flush=True)
        try:
            if step == 'sheet':
                # Re-planning one programme would delete the others' work folders, so it is all or nothing.
                run([py, demo / 'sheet_to_news.py', '--date', args.date] + (['--csv', args.csv] if args.csv else []))
                continue
            progs = ready_programmes(args.date, only)
            dirs = [WORK / args.date / p['id'] for p in progs]
            if step == 'images':
                run([py, demo / 'fetch_images.py', '--workdir', *dirs])
            elif step == 'prepare':
                run([vieneu_python(), demo / 'prepare.py', '--provider', 'vieneu', '--voice', args.voice, '--workdir', *dirs])
            elif step == 'render':
                jobs = [{'props': str(d / 'props-vieneu.json'), 'output': str(out_dir / f'{d.name}.mp4')} for d in dirs]
                jobs_file = WORK / args.date / 'render-jobs.json'
                jobs_file.write_text(json.dumps(jobs, ensure_ascii=False, indent=2))
                try:
                    run(['node', demo / 'render.mjs', '--batch', jobs_file])
                finally:  # keep the sources of every video that did render
                    for d in dirs:
                        if (out_dir / f'{d.name}.mp4').exists() and (d / 'sources.md').exists():
                            shutil.copyfile(d / 'sources.md', out_dir / f'{d.name}-nguon.md')
            elif step == 'verify':
                for d in dirs:
                    video = out_dir / f'{d.name}.mp4'
                    if video.exists():
                        run([py, demo / 'verify.py', video, d / 'props-vieneu.json'])
                    else:
                        print(f'CẢNH BÁO: chưa có video {video.name}', flush=True)
        except subprocess.CalledProcessError as e:
            print(f'\nTHẤT BẠI ở bước "{LABELS[step]}" (mã {e.returncode})', flush=True)
            failed.append(step)
            if step != 'render':  # a partly failed batch render can still be checked
                return 1
    print(f'\nXONG sau {time.monotonic() - started:.0f} giây', flush=True)
    if 'render' in steps:
        print('THƯ MỤC VIDEO', out_dir, flush=True)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())

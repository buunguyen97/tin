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

from edition import BUSY, VOICES, WORK, pipeline_lock
from loudness import try_normalize
from reup import ff

demo = Path(__file__).resolve().parent
project = demo.parent
STEPS = ['sheet', 'images', 'prepare', 'render', 'verify']
LABELS = {'sheet': 'Đọc Google Sheet, chia tin theo video', 'images': 'Tải ảnh bài báo',
          'prepare': 'Đọc giọng + dựng intro, thẻ tin', 'render': 'Render video', 'verify': 'Kiểm tra video'}


def vieneu_python():
    venv = Path('.venv', 'Scripts', 'python.exe') if sys.platform == 'win32' else Path('.venv', 'bin', 'python')
    default = project.parent / 'VieNeu-TTS' / venv
    if not default.exists() and (project / 'VieNeu-TTS' / venv).exists():  # cloned inside the project
        default = project / 'VieNeu-TTS' / venv
    path = Path(os.environ.get('VIENEU_PYTHON', default))
    if not path.exists():
        sys.exit(f'Không thấy Python của VieNeu tại {path}; đặt biến VIENEU_PYTHON')
    return str(path)


def day_output(day):
    return project / 'output' / 'daily' / day


def remove_outputs(out_dir, pid, frames_only=False):
    """Delete a video's files (or only its QA frames, whose count follows the number of stories)."""
    names = [] if frames_only else [f'{pid}.mp4', f'{pid}-preview.png', f'{pid}-nguon.md',
                                    f'{pid}-verification.txt', f'{pid}-contact-sheet.jpg']
    for f in [out_dir / n for n in names] + list(out_dir.glob(f'{pid}-frame-*.jpg')):
        f.unlink(missing_ok=True)


def remove_stale_videos(day, out_dir):
    """After re-planning, a video whose programme no longer has enough stories must not linger."""
    if not out_dir.exists():
        return
    ready = {p['id'] for p in json.loads((WORK / day / 'plan.json').read_text())['programmes'] if p['status'] == 'ready'}
    for video in out_dir.glob('*.mp4'):
        if video.stem not in ready:
            print(f'Xoá video cũ không còn trong kế hoạch: {video.name}', flush=True)
            remove_outputs(out_dir, video.stem)


def burn_captions(video, work_dir):
    """Draw prepare.py's voice-following captions into a rendered video. If it fails the video still counts, without them."""
    if not (work_dir / 'captions.ass').exists():
        return
    done = video.with_name(video.stem + '.sub' + video.suffix)
    fonts = Path(os.path.relpath(demo / 'fonts', work_dir)).as_posix()  # relative: a drive letter's colon breaks the filter
    try:
        ff('-i', video.resolve(), '-vf', f'ass=captions.ass:fontsdir={fonts}', '-c:v', 'libx264', '-crf', '18', '-pix_fmt', 'yuv420p',
           '-c:a', 'copy', '-movflags', '+faststart', done.resolve(), cwd=work_dir)
        done.replace(video)
    except subprocess.CalledProcessError as e:
        print(f'CẢNH BÁO: không gắn được phụ đề vào {video.name}: {e}', flush=True)
    finally:
        done.unlink(missing_ok=True)


def run(cmd):
    print('$', ' '.join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], cwd=project, check=True)


def still_good(day, pid, made_with):
    """Was this programme's video finished from the very news.json in its folder, with these settings? The sheet step keeps
    the folder of a programme whose stories did not change, and the verify step leaves done.json there."""
    done = WORK / day / pid / 'done.json'
    try:
        return json.loads(done.read_text()) == made_with and (day_output(day) / f'{pid}.mp4').exists()
    except (OSError, ValueError):
        return False


def ready_programmes(day, only, made_with=None):
    """The programmes to work on. With `made_with` (a full run), the ones whose finished video still stands are left out;
    the list is then empty when nothing changed."""
    plan_file = WORK / day / 'plan.json'
    if not plan_file.exists():
        sys.exit(f'Chưa có kế hoạch cho ngày {day}; chạy bước sheet trước')
    ready = [p for p in json.loads(plan_file.read_text())['programmes'] if p['status'] == 'ready']
    if only:
        ready = [p for p in ready if p['id'] in only]
    if not ready:
        sys.exit('Không có video nào để làm')
    if made_with is not None:
        kept = [p['id'] for p in ready if p.get('unchanged') and still_good(day, p['id'], made_with)]
        if kept:
            print('Giữ nguyên video không đổi tin: ' + ', '.join(kept), flush=True)
        ready = [p for p in ready if p['id'] not in kept]
    return ready


def main(argv=None):
    with pipeline_lock() as acquired:
        if not acquired:
            print('Đang có một lượt tạo video khác chạy (Studio hoặc tự động); thử lại sau.', flush=True)
            return BUSY
        return _main(argv)


def _main(argv=None):
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
    made_with = {'voice': args.voice, 'speed': config.get('speed', 1.0), 'short': bool(config.get('short_format'))}
    for n, step in enumerate(steps, 1):
        print(f'\n== BƯỚC {n}/{len(steps)}: {LABELS[step]}', flush=True)
        try:
            if step == 'sheet':
                # Re-planning one programme would delete the others' work folders, so it is all or nothing.
                try:
                    run([py, demo / 'sheet_to_news.py', '--date', args.date] + (['--csv', args.csv] if args.csv else []))
                finally:  # also when no programme has enough stories any more
                    if (WORK / args.date / 'plan.json').exists():
                        remove_stale_videos(args.date, out_dir)
                continue
            # Naming programmes asks for them to be made again; a whole run skips the ones the sheet step left untouched.
            progs = ready_programmes(args.date, only, None if only else made_with)
            dirs = [WORK / args.date / p['id'] for p in progs]
            if not dirs:
                print('Không có video nào đổi tin, không cần làm lại', flush=True)
            elif step == 'images':
                run([py, demo / 'fetch_images.py', '--workdir', *dirs])
            elif step == 'prepare':
                import imageio_ffmpeg  # VieNeu's environment has no ffmpeg; prepare.py needs one to change speed
                os.environ.setdefault('FFMPEG_BINARY', imageio_ffmpeg.get_ffmpeg_exe())
                run([vieneu_python(), demo / 'prepare.py', '--provider', 'vieneu', '--voice', args.voice,
                     '--speed', config.get('speed', 1.0), *(['--short'] if config.get('short_format') else []), '--workdir', *dirs])
            elif step == 'render':
                jobs = [{'props': str(d / 'props-vieneu.json'), 'output': str(out_dir / f'{d.name}.mp4')} for d in dirs]
                jobs_file = WORK / args.date / 'render-jobs.json'
                jobs_file.write_text(json.dumps(jobs, ensure_ascii=False, indent=2))
                render_began = time.time()
                try:
                    run(['node', demo / 'render.mjs', '--batch', jobs_file])
                finally:  # keep the sources of every video that did render
                    for d in dirs:
                        if (out_dir / f'{d.name}.mp4').exists():
                            if (out_dir / f'{d.name}.mp4').stat().st_mtime >= render_began:  # not an older video whose new render failed
                                burn_captions(out_dir / f'{d.name}.mp4', d)
                            try_normalize(out_dir / f'{d.name}.mp4')
                            if (d / 'sources.md').exists():
                                shutil.copyfile(d / 'sources.md', out_dir / f'{d.name}-nguon.md')
            elif step == 'verify':
                for d in dirs:
                    video = out_dir / f'{d.name}.mp4'
                    if video.exists():
                        remove_outputs(out_dir, d.name, frames_only=True)
                        (d / 'done.json').unlink(missing_ok=True)
                        run([py, demo / 'verify.py', video, d / 'props-vieneu.json'])
                        if 'render' not in failed:
                            (d / 'done.json').write_text(json.dumps(made_with, ensure_ascii=False))
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

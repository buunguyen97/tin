"""Reup a social video (X, TikTok, YouTube, Facebook…) as a vertical news clip with a Vietnamese voice-over.

  fetch   download with yt-dlp, find where the footage ends (outro cards), sample frames for Claude
  script  Claude Code (`claude -p`, the signed-in Claude plan, no API key) writes title, sentences and caption
  build   VieNeu reads each sentence; ffmpeg composes the clip; overlays and props for the player
  render  demo/render.mjs -> output/reup/<id>.mp4

  .venv-demo/Scripts/python.exe demo/reup.py all <url>            # every step
  .venv-demo/Scripts/python.exe demo/reup.py script <id> --note "ngắn lại"
  .venv-demo/Scripts/python.exe demo/reup.py build <id>           # after editing work/reup/<id>/script.json
  .venv-demo/Scripts/python.exe demo/reup.py render <id>

Files in demo/work/reup/<id>/: source.mp4, info.json, analysis.json, frames.jpg, script.json,
voice.wav, audio.wav, background.mp4, sub-N.svg, props.json.
"""
import argparse, asyncio, hashlib, html, json, math, os, re, shutil, subprocess, sys, wave
from pathlib import Path

from edition import WORK, pipeline_lock

demo = Path(__file__).resolve().parent
project = demo.parent
REUP = WORK / 'reup'
OUTPUT = project / 'output' / 'reup'
FPS = 25
LEAD, GAP, TAIL = .3, .25, .8  # seconds of silence before, between and after the sentences
SYLLABLES_PER_SECOND = 5  # VieNeu at speed 1.25, gaps included
NARRATION_SECONDS = 60  # a longer clip plays on under its own sound after the voice
MAX_SENTENCE = 25  # syllables: about five seconds, three subtitle lines
SCRIPT_ATTEMPTS = 3  # Claude's first answer and up to two corrections
MAX_SECONDS = 90


def ffmpeg():
    if os.environ.get('FFMPEG_BINARY'):
        return os.environ['FFMPEG_BINARY']
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def ff(*args):
    subprocess.run([ffmpeg(), '-y', '-loglevel', 'error', *map(str, args)], check=True)


def ff_log(*args):
    """stderr of an ffmpeg analysis pass (filters print their findings there)."""
    return subprocess.run([ffmpeg(), '-hide_banner', *map(str, args), '-f', 'null', '-'],
                          capture_output=True, text=True, encoding='utf8', errors='replace').stderr


def settings():
    """The "reup" block of config.json, falling back to the daily videos' voice and speed."""
    config = json.loads((demo / 'config.json').read_text()) if (demo / 'config.json').exists() else {}
    reup = config.get('reup', {})
    return {'voice': reup.get('voice') or config.get('voice', 'Ngọc Huyền'), 'speed': reup.get('speed') or config.get('speed', 1.25),
            'original_volume': reup.get('original_volume', .25), 'label': reup.get('label', 'TIN NÓNG')}


def dotenv():
    """KEY=value lines of the project's .env (Telegram token, Claude plan token)."""
    path = project / '.env'
    if not path.exists():
        return {}
    pairs = (line.split('=', 1) for line in path.read_text().splitlines() if '=' in line and not line.lstrip().startswith('#'))
    return {k.strip(): v.strip() for k, v in pairs}


def workdir(key):
    path = Path(key)
    return path.resolve() if path.is_dir() else REUP / key


# --- fetch -----------------------------------------------------------------------------------------

def footage_end(src, duration):
    """Where the footage stops: many posts end on a silent logo card. A silence that runs to the end of
    the file and starts at a scene cut marks it; otherwise the whole video is footage."""
    cuts = [float(t) for t in re.findall(r'pts_time:([\d.]+)', ff_log('-i', src, '-vf', "select='gt(scene,0.3)',showinfo"))]
    silence = re.findall(r'silence_start: ([\d.]+)', ff_log('-i', src, '-af', 'silencedetect=n=-40dB:d=1.5'))
    if silence and duration - float(silence[-1]) >= 1.5:
        start = float(silence[-1])
        near = [c for c in cuts if abs(c - start) < 1.2]
        if near and near[0] > 3:
            return round(near[0], 2)
    return round(duration, 2)


def loudest(src, end):
    """Time of the loudest quarter-second before `end` (a blast, a crash): the moment worth replaying.
    The last half second is left out: the cut to an outro card often carries a whoosh."""
    # RMS over about a quarter second: astats resets every 12 audio frames of 1024 samples
    log = ff_log('-i', src, '-t', max(1, end - .5), '-af', 'astats=metadata=1:reset=12,ametadata=print:key=lavfi.astats.Overall.RMS_level')
    levels = re.findall(r'pts_time:([\d.]+)\s*\n.*?RMS_level=(-?[\d.]+|-inf)', log)
    levels = [(float(level), -float(t)) for t, level in levels if level != '-inf']
    return -max(levels)[1] if levels else end / 3  # the earliest of equally loud moments


def contact_sheet(src, end, out, count=12):
    """Frames across the footage, each labelled with its time, for Claude to look at."""
    from PIL import Image, ImageDraw, ImageFont
    tiles = []
    for i in range(count):
        t = end * (i + .5) / count
        frame = out.with_name(f'frame-{i}.jpg')
        ff('-ss', f'{t:.2f}', '-i', src, '-frames:v', 1, '-vf', 'scale=320:-2', frame)
        if frame.exists():
            tiles.append((t, Image.open(frame).convert('RGB'))); frame.unlink()
    if not tiles:
        raise RuntimeError('Không đọc được khung hình nào của video')
    w, h = tiles[0][1].size; cols = 6 if w < h else 4; rows = math.ceil(len(tiles) / cols)
    sheet = Image.new('RGB', (cols * w, rows * h), 'black'); draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype(str(demo / 'fonts' / 'BeVietnamPro-Bold.ttf'), 22)
    for i, (t, im) in enumerate(tiles):
        x, y = i % cols * w, i // cols * h
        sheet.paste(im.resize((w, h)), (x, y))
        draw.rectangle((x, y, x + 86, y + 34), fill='black'); draw.text((x + 8, y + 4), f'{t:.1f}s', fill='#FFD21F', font=font)
    sheet.save(out, quality=85)


def fetch(url):
    """Download the post's video into work/reup/<id>/ (once) and analyse it."""
    import yt_dlp
    REUP.mkdir(parents=True, exist_ok=True)
    options = {'quiet': True, 'no_warnings': True, 'noprogress': True, 'noplaylist': True, 'ffmpeg_location': ffmpeg(),
               'format': 'bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b', 'merge_output_format': 'mp4'}
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)
        if info.get('_type') == 'playlist':  # a post with several videos: the first one
            info = info['entries'][0]
        wd = REUP / re.sub(r'[^\w-]', '_', f"{info['extractor_key']}-{info['id']}".lower())
        wd.mkdir(exist_ok=True)
        src = wd / 'source.mp4'
        if not src.exists():
            with yt_dlp.YoutubeDL({**options, 'outtmpl': str(wd / 'source.%(ext)s')}) as one:
                one.download([info.get('webpage_url') or url])
    keep = ['id', 'title', 'description', 'uploader', 'uploader_id', 'channel', 'duration', 'width', 'height', 'upload_date', 'webpage_url', 'extractor_key']
    (wd / 'info.json').write_text(json.dumps({'url': url, **{k: info.get(k) for k in keep}}, ensure_ascii=False, indent=2))
    duration = float(info.get('duration') or 0) or probe_duration(src)
    end = min(footage_end(src, duration), MAX_SECONDS)
    peak = loudest(src, end)
    width, height = probe_size(src)
    analysis = {'duration': duration, 'clip_end': end, 'peak': round(peak, 2),
                'replay': [round(max(0, peak - 1.2), 2), round(min(end, peak + 1.6), 2)], 'width': width, 'height': height}
    (wd / 'analysis.json').write_text(json.dumps(analysis, indent=2))
    contact_sheet(src, end, wd / 'frames.jpg')
    return wd


def probe_duration(src):
    m = re.search(r'Duration: (\d+):(\d+):([\d.]+)', ff_log('-i', src))
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])


def probe_size(src):
    m = re.search(r'Video: .*?, (\d{2,5})x(\d{2,5})', ff_log('-i', src, '-t', 0))
    return int(m[1]), int(m[2])


# --- script ----------------------------------------------------------------------------------------

PROMPT = """Bạn là biên tập viên video tin tức ngắn kiểu TikTok của một tờ báo Việt Nam.
Một video từ mạng xã hội sẽ được đăng lại, kèm tiêu đề trên màn hình và giọng đọc tiếng Việt.

Bài đăng gốc:
- Tài khoản: {uploader}
- Nội dung bài đăng: {description}
- Link: {url}
Video dùng {clip_end:.1f} giây đầu (phần sau là màn hình logo nếu có). Tiếng to nhất ở giây {peak:.1f}.
Ảnh {frames} ghép các khung hình của video, góc mỗi ô ghi thời điểm. Hãy mở ảnh bằng công cụ Read và xem kỹ.

Viết kịch bản theo các quy tắc:
- Chỉ nói điều thấy trong video hoặc nói trong bài đăng. Không bịa tên, số liệu, thương vong. Điều chỉ bài đăng khẳng định
  thì ghi rõ nguồn ("theo ..."). Dùng từ trung tính thay cho từ ngữ thiên kiến của bài gốc.
- {fewest} đến {most_sentences} câu, mỗi câu không quá {max_sentence} tiếng (âm tiết); tổng khoảng {syllables} tiếng, không quá {most} tiếng.
  Câu đầu là câu dẫn hấp dẫn.
  Thứ tự các câu theo diễn biến trong video, để lời đọc khớp hình.
- Câu cuối nhắc nguồn: video do ai đăng, trên mạng nào.
- Câu đọc chỉ dùng chữ: số viết bằng chữ ("hai mươi người"), không viết tắt, không ký hiệu, từ nước ngoài
  thay bằng từ tiếng Việt khi có (drone -> máy bay không người lái). Tên riêng nước ngoài giữ nguyên.
- Tiêu đề: 1 hoặc 2 dòng VIẾT HOA, mỗi dòng không quá 24 ký tự, gây tò mò nhưng đúng sự thật.
- Nhãn: 1 đến 3 từ VIẾT HOA (ví dụ TIN NÓNG, TIN QUỐC TẾ, CLIP HOT).
- Caption để đăng TikTok: 1 đến 2 câu, kèm 3 đến 5 hashtag tiếng Việt không dấu.
{revision}
Chỉ trả về đúng một khối JSON, không giải thích:
{{"label": "...", "title": ["...", "..."], "sentences": ["...", "..."], "source": "<tên tài khoản> / <tên mạng>", "caption": "..."}}
"""


def claude_bin():
    found = os.environ.get('CLAUDE_BIN') or shutil.which('claude')
    if not found:
        raise RuntimeError('Không thấy lệnh claude (Claude Code); cài Claude Code hoặc đặt CLAUDE_BIN')
    return found


def ask_claude(prompt, wd):
    """One `claude -p` answer, as the JSON object in it. Only Read is allowed: Claude looks at the frames
    and answers; this script writes the file."""
    # A clean environment: started from inside a Claude Code session, the child would inherit that
    # session's connection settings instead of the plan this machine is signed in with.
    # CLAUDE_CONFIG_DIR in .env: a Claude Code profile of its own, signed in to the Claude plan, so the
    # global settings (an apiKeyHelper or another endpoint) don't apply. CLAUDE_CODE_OAUTH_TOKEN, from
    # `claude setup-token`, also works.
    env = {k: v for k, v in os.environ.items() if not k.startswith(('CLAUDE', 'ANTHROPIC'))}
    for key in ('CLAUDE_CONFIG_DIR', 'CLAUDE_CODE_OAUTH_TOKEN'):
        if dotenv().get(key):
            env[key] = os.path.expandvars(dotenv()[key])
    run = subprocess.run([claude_bin(), '-p', '--output-format', 'json', '--allowedTools', 'Read', '--max-turns', '6'],
                         input=prompt, capture_output=True, text=True, encoding='utf8', errors='replace', cwd=wd, env=env, timeout=600)
    try:
        answer = json.loads(run.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f'Claude không trả lời được: {(run.stderr or run.stdout)[-500:]}')
    if answer.get('is_error'):
        raise RuntimeError(f"Claude báo lỗi: {answer.get('result') or answer.get('subtype')}")
    found = re.search(r'\{.*\}', answer.get('result', ''), re.S)
    if not found:
        raise RuntimeError(f"Claude không trả về JSON: {answer.get('result', '')[:500]}")
    return json.loads(found[0])


def problems(script, most):
    """What breaks the rules in a script, in words Claude is asked to fix; empty when it is fine."""
    found = []
    sentences = script.get('sentences') or []
    if not sentences or not script.get('title'):
        return ['Kịch bản thiếu tiêu đề hoặc câu đọc.']
    for i, sentence in enumerate(sentences):
        n = len(sentence.split())
        if n > MAX_SENTENCE:
            found.append(f'Câu {i + 1} dài {n} tiếng, quá {MAX_SENTENCE} tiếng: tách thành nhiều câu ngắn hoặc bỏ bớt chi tiết.')
        if re.search(r'\d', sentence):
            found.append(f'Câu {i + 1} còn chữ số: viết số bằng chữ.')
    total = sum(len(s.split()) for s in sentences)
    if total > most:
        found.append(f'Tổng {total} tiếng, quá {most} tiếng: rút gọn.')
    titles = [script['title']] if isinstance(script['title'], str) else script['title']
    if len(titles) > 2 or any(len(t) > 30 for t in titles):
        found.append('Tiêu đề phải có 1 hoặc 2 dòng, mỗi dòng không quá 24 ký tự.')
    return found


def write_script(wd, note=None):
    """Ask Claude Code, signed in with the Claude plan, for script.json. With `note`, revise the current one.
    An answer that breaks the length rules goes back to Claude with what is wrong, at most twice."""
    info = json.loads((wd / 'info.json').read_text()); analysis = json.loads((wd / 'analysis.json').read_text())
    seconds = min(max(analysis['clip_end'], 10), NARRATION_SECONDS)
    syllables = round(seconds * SYLLABLES_PER_SECOND); most = round(syllables * 1.15)
    base = PROMPT.format(uploader=info.get('uploader') or info.get('channel') or '', description=(info.get('description') or info.get('title') or '').strip(),
                         url=info['url'], clip_end=analysis['clip_end'], peak=analysis['peak'], frames=(wd / 'frames.jpg').as_posix(),
                         syllables=syllables, most=most, max_sentence=MAX_SENTENCE,
                         fewest=max(3, math.ceil(syllables / MAX_SENTENCE)), most_sentences=max(5, math.ceil(syllables / 15)), revision='{revision}')
    current = None
    if note and (wd / 'script.json').exists():
        current = json.loads((wd / 'script.json').read_text())
    for attempt in range(SCRIPT_ATTEMPTS):
        revision = ''
        if current is not None:
            draft = {k: v for k, v in current.items() if k in ('label', 'title', 'sentences', 'source', 'caption')}
            revision = f'\nĐây là bản hiện tại:\n{json.dumps(draft, ensure_ascii=False)}\nCần sửa: "{note}". Sửa theo yêu cầu, giữ nguyên các quy tắc trên.\n'
        script = ask_claude(base.replace('{revision}', revision), wd)
        wrong = problems(script, most)
        if not wrong:
            break
        print(f'  kịch bản chưa đạt (lần {attempt + 1}): {" ".join(wrong)}', flush=True)
        current, note = script, ' '.join(wrong)
    else:
        if not script.get('sentences') or not script.get('title'):
            raise RuntimeError('Kịch bản thiếu tiêu đề hoặc câu đọc')
        script['warning'] = ' '.join(wrong)  # shown to the editor with the script
    script['title'] = [script['title']] if isinstance(script['title'], str) else script['title'][:2]
    script.setdefault('label', settings()['label']); script.setdefault('source', info.get('uploader') or '')
    script.setdefault('caption', '')
    (wd / 'script.json').write_text(json.dumps(script, ensure_ascii=False, indent=2))
    return script


def script_text(script):
    """The script as a message for the editor."""
    lines = [f"[{script['label']}] {' '.join(script['title'])}", '']
    lines += [f'{i + 1}. {s}' for i, s in enumerate(script['sentences'])]
    lines += ['', f"Nguồn: {script['source']}"]
    if script.get('caption'):
        lines += ['', f"Caption: {script['caption']}"]
    if script.get('warning'):
        lines += ['', f"⚠️ Claude chưa sửa hết: {script['warning']}"]
    return '\n'.join(lines)


# --- build (VieNeu environment) --------------------------------------------------------------------

def vieneu_python():
    venv = Path('.venv', 'Scripts', 'python.exe') if sys.platform == 'win32' else Path('.venv', 'bin', 'python')
    default = next((p / 'VieNeu-TTS' / venv for p in (project, project.parent) if (p / 'VieNeu-TTS' / venv).exists()), project / 'VieNeu-TTS' / venv)
    return str(Path(os.environ.get('VIENEU_PYTHON', default)))


def build(wd):
    """Voice and overlays need VieNeu, which lives in its own environment: run `build` there."""
    env = {**os.environ, 'FFMPEG_BINARY': ffmpeg(), 'PYTHONUTF8': '1'}
    subprocess.run([vieneu_python(), '-X', 'utf8', __file__, '_build', str(wd)], check=True, env=env)


def frames(seconds):
    return math.ceil(seconds * FPS - 1e-6)


def box(width, height):
    """Where the clip sits in the 1080x1920 frame: x, y, w, h. Portrait clips fill a framed column
    under the title; landscape ones run edge to edge below it."""
    if width >= height:
        h = round(1080 * height / width / 2) * 2
        return 0, 520, 1080, h
    w = min(792, round(1408 * width / height / 2) * 2); h = round(w * height / width / 2) * 2
    return (1080 - w) // 2, 420, w, h


async def voice_lines(sentences, conf):
    from prepare import Voice, assets
    from tts_audio import audio_duration, cache_path
    name, speed = conf['voice'], conf['speed']
    model = 'v3turbo-fp32-vieneu-3.8.3' + (f'-x{speed:g}' if speed != 1 else '')
    sample = demo / 'voices' / f'{name}.wav'
    key = f'{name}:{hashlib.sha256(sample.read_bytes()).hexdigest()[:12]}' if sample.exists() else name
    voice = Voice('vieneu', name, 4, speed); files = []
    for s in sentences:
        f = cache_path(assets, provider='vieneu', voice=key, model=model, text=s)
        if not f.exists():
            tmp = f.with_name(f.stem + '.part' + f.suffix)  # interrupted synthesis never becomes a cache hit
            await voice.say(s, tmp, name); tmp.replace(f)
        print(f'  {audio_duration(f):.2f}s  {s}', flush=True); files.append(f)
    return files


def join_voice(files, out):
    """The sentences on the frame grid with short silences between; returns [(start, end)] and the voice length."""
    with wave.open(str(files[0])) as w0:
        params = w0.getparams()
    rate, step = params.framerate, params.sampwidth * params.nchannels
    data = bytearray(); times = []; pos = frames(LEAD)
    pad = lambda: data.extend(b'\0' * max(0, round(pos / FPS * rate) * step - len(data)))
    pad()
    for f in files:
        with wave.open(str(f)) as w:
            pcm = w.readframes(w.getnframes())
        start = pos; data.extend(pcm); end = start + frames(len(pcm) / step / rate)
        times.append((start / FPS, end / FPS)); pos = end + frames(GAP); pad()
    pos = frames(times[-1][1] + TAIL); pad()
    with wave.open(str(out), 'wb') as w:
        w.setparams(params); w.writeframes(bytes(data))
    return times, pos / FPS


def timeline(analysis, voice):
    """How the footage covers the voice: video length, slow-down factor, whether to add the replay."""
    end = analysis['clip_end']; total = max(voice, end)
    stretch = total / end if end < total <= end * 1.15 else 1  # a little short: slow it down unnoticeably
    replay = total > end * 1.15  # much shorter: replay the loudest moment at half speed, then hold the last frame
    return total, stretch, replay


def compose_video(src, analysis, total, stretch, replay, out):
    from prepare import W, H
    x, y, w, h = box(analysis['width'], analysis['height']); end = analysis['clip_end']; a, b = analysis['replay']
    graph = f'[0:v]trim=0:{end},setpts={stretch:.4f}*(PTS-STARTPTS)[a];'
    graph += f'[0:v]trim={a}:{b},setpts=2*(PTS-STARTPTS)[r];[a][r]concat=n=2:v=1,' if replay else '[a]'
    graph += (f'fps={FPS},tpad=stop_mode=clone:stop_duration={MAX_SECONDS},trim=0:{total},split[v1][v2];'
              f'[v1]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=40:3,eq=brightness=-0.28[bg];'
              f'[v2]scale={w}:{h}:flags=lanczos,setsar=1[fg];[bg][fg]overlay={x}:{y}[v]')
    ff('-i', src, '-filter_complex', graph, '-map', '[v]', '-an', '-c:v', 'libx264', '-crf', '17', '-pix_fmt', 'yuv420p', out)


def mix_audio(src, voice, analysis, total, stretch, volume, out):
    """The voice over the clip's own sound, turned down; the replay and held frame stay quiet."""
    has_audio = 'Audio:' in ff_log('-i', src, '-t', 0)
    if not has_audio:
        ff('-i', voice, '-af', f'apad,atrim=0:{total}', '-ar', 48000, '-ac', 2, out); return
    ff('-i', voice, '-i', src, '-filter_complex',
       f'[1:a]atrim=0:{analysis["clip_end"]},asetpts=PTS-STARTPTS,atempo={1 / stretch:.4f},volume={volume},apad[o];'
       f'[0:a]apad[v];[v][o]amix=inputs=2:duration=shortest:normalize=0,atrim=0:{total}[m]',
       '-map', '[m]', '-ar', 48000, '-ac', 2, out)


def overlay(script, sentence, clip):
    """Title, tag and source above the clip, a yellow frame round it, the sentence as a subtitle."""
    from prepare import W, H, INK, YELLOW, FAMILY, CONDENSED, font_style, condensed_style, text, fit, text_width, _wrap_condensed
    x, y, w, h = clip; left = 80; top = 160
    lines = script['title']; size = min(fit(l, 70, 900, 920) for l in lines)
    chip = script['label']; tw, th, sk = round(text_width(chip, 30, 800, True) + 56), 54, 16
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">', font_style(), condensed_style(),
         '<defs><filter id="sh" x="-10%" y="-20%" width="120%" height="140%"><feDropShadow dx="0" dy="4" stdDeviation="5" flood-color="#000" flood-opacity=".8"/></filter>'
         f'<linearGradient id="top" x2="0" y2="1"><stop stop-color="{INK}" stop-opacity=".95"/><stop offset=".8" stop-color="{INK}" stop-opacity=".8"/><stop offset="1" stop-color="{INK}" stop-opacity="0"/></linearGradient></defs>',
         f'<rect width="{W}" height="{y + 10}" fill="url(#top)"/>',
         f'<path d="M{left + sk} {top}H{left + sk + tw}L{left + tw} {top + th}H{left}Z" fill="{YELLOW}"/>',  # slanted tag, as on the intro card
         text(round(left + sk / 2 + tw / 2), top + 38, chip, 30, INK, 800, 'middle', ' font-style="italic"'),
         # the source on the tag's row, clear of the original's own logo at the top of the clip
         text(1000, top + 38, f'Nguồn: {script["source"]}', fit(f'Nguồn: {script["source"]}', 26, 700, 1000 - left - tw - 40), '#FFFFFF', 700, 'end', ' fill-opacity=".9"')]
    cy = top + th + 12
    for line in lines:
        cy += round(size * 1.08)
        s.append(f'<text x="{left}" y="{cy}" fill="#FFFFFF" font-family="{FAMILY}" font-size="{size}" font-weight="900" filter="url(#sh)">{html.escape(line)}</text>')
    if w < W:
        s.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="none" stroke="{YELLOW}" stroke-width="5"/>')
    ss = 64
    while len(sub := _wrap_condensed(sentence, 780, ss)) > 3:
        ss -= 4
    lh = round(ss * 1.18); base = 1400 - (len(sub) - 1) * lh  # ends above TikTok's caption area
    outline = f'font-family="{CONDENSED}" font-weight="700" fill="#FFFFFF" stroke="#000000" stroke-linejoin="round" paint-order="stroke" text-anchor="middle"'
    for j, line in enumerate(sub):
        s.append(f'<text x="{W // 2}" y="{base + j * lh}" font-size="{ss}" stroke-width="{round(ss * .2)}" {outline}>{html.escape(line)}</text>')
    return ''.join(s + ['</svg>'])


def _build(wd):
    from prepare import W, H, BASE, url
    script = json.loads((wd / 'script.json').read_text()); analysis = json.loads((wd / 'analysis.json').read_text())
    conf = settings(); src = wd / 'source.mp4'
    files = asyncio.run(voice_lines(script['sentences'], conf))
    times, voice = join_voice(files, wd / 'voice.wav')
    total, stretch, replay = timeline(analysis, voice)
    print(f'  giọng {voice:.2f}s, video gốc {analysis["clip_end"]}s -> {total:.2f}s', flush=True)
    compose_video(src, analysis, total, stretch, replay, wd / 'background.mp4')
    mix_audio(src, wd / 'voice.wav', analysis, total, stretch, conf['original_volume'], wd / 'audio.wav')
    clip = box(analysis['width'], analysis['height']); segments = []
    for old in wd.glob('sub-*.svg'):
        old.unlink()
    for i, (sentence, (start, _)) in enumerate(zip(script['sentences'], times)):
        seg_start = 0 if i == 0 else start
        seg_end = times[i + 1][0] if i + 1 < len(times) else total
        sub = wd / f'sub-{i + 1}.svg'; sub.write_text(overlay(script, sentence, clip))
        segments.append({'id': i + 1, 'mood': 'neutral', 'type': 'text', 'style': 'bottom',
                         'texts': [{'type': 'text', 'text': sentence, 'line_id': i + 1, 'who': 'default', 'start': seg_start, 'end': seg_end}],
                         'cameraMovement': 'none',
                         'images': [{'type': 'video', 'url': url(wd / 'background.mp4'), 'startFrom': seg_start, 'endAt': seg_end,
                                     'videoAsset': {'id': wd.name, 'title': ' '.join(script['title']), 'duration': total * 1000, 'streamUrls': {}}}],
                         'overlay': {'url': url(sub)}, 'start': seg_start, 'end': seg_end})
    manuscript = {'meta': {'title': ' '.join(script['title']), 'pubdate': '2026-01-01T00:00:00Z', 'byline': script['source'], 'id': 1,
                           'uniqueId': f'reup-{wd.name}', 'description': ' '.join(script['sentences']), 'audio': {'src': url(wd / 'audio.wav')}},
                  'segments': segments}
    props = {'manuscripts': [manuscript], 'width': W, 'height': H, 'voice': True, 'backgroundMusic': False, 'disabledLogo': True,
             'playerConfig': {'assetBaseUrl': BASE, 'logo': 'unused.svg', 'defaultCameraMovements': ['none'],
                              'styles': {'all': {'captions': {'container': 'display:none;'}, 'progress': {'active': 'display:none;', 'inactive': 'display:none;'}}}}}
    (wd / 'props.json').write_text(json.dumps(props, ensure_ascii=False, indent=2))


# --- render ----------------------------------------------------------------------------------------

def render(wd):
    out = OUTPUT / f'{wd.name}.mp4'
    node = shutil.which('node') or 'node'
    subprocess.run([node, str(demo / 'render.mjs'), str(wd / 'props.json'), str(out)], check=True, cwd=project)
    return out


def smaller_copy(video, limit_mb=48):
    """A copy that fits `limit_mb` (Telegram bots send at most 50 MB), or the video itself if it already does.
    The bitrate is set from the duration; the full-quality file stays in output/reup/."""
    if video.stat().st_size <= limit_mb * 1024 * 1024:
        return video
    out = video.with_name(video.stem + '-nho.mp4')
    if out.exists() and out.stat().st_mtime > video.stat().st_mtime:
        return out
    audio_kbps = 128
    video_kbps = int(limit_mb * 8192 * .95 / probe_duration(video)) - audio_kbps
    ff('-i', video, '-c:v', 'libx264', '-preset', 'medium', '-b:v', f'{video_kbps}k', '-maxrate', f'{video_kbps}k', '-bufsize', f'{2 * video_kbps}k',
       '-c:a', 'aac', '-b:a', f'{audio_kbps}k', '-movflags', '+faststart', out)
    return out


def produce(wd):
    """build + render under the lock shared with the daily videos (both use port 8765 and the VieNeu model)."""
    with pipeline_lock() as acquired:
        if not acquired:
            raise BlockingIOError('Đang có một lượt tạo video khác chạy')
        build(wd)
        return render(wd)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Đăng lại video mạng xã hội kèm giọng đọc tiếng Việt')
    parser.add_argument('step', choices=['all', 'fetch', 'script', 'build', 'render', '_build'])
    parser.add_argument('target', help='link video (all, fetch) hoặc mã / thư mục trong demo/work/reup')
    parser.add_argument('--note', help='yêu cầu sửa kịch bản (bước script)')
    args = parser.parse_args(argv)
    if args.step == '_build':
        return _build(Path(args.target))
    wd = fetch(args.target) if args.step in ('all', 'fetch') else workdir(args.target)
    print('Thư mục:', wd, flush=True)
    if args.step in ('all', 'script'):
        print(script_text(write_script(wd, args.note)), flush=True)
    if args.step == 'build':
        build(wd)
    if args.step == 'render':
        print('Video:', render(wd))
    if args.step == 'all':
        print('Video:', produce(wd))


if __name__ == '__main__':
    main()

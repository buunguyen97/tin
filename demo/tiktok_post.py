"""Post finished videos to TikTok with autotok (a saved browser login, not TikTok's official API).

Log in once: .venv-demo/Scripts/autotok.exe login -n <name>, then set "tiktok_account" in the "reup" block of
config.json. The session stays in ~/.autotok, outside this project.

Every upload is a real post, so a tiktok.json remembers what has gone out: demo/work/reup/<id>/ for reup videos,
output/daily/<day>/ for news videos (their work folders are rebuilt whenever the sheet changes).
"""
import hashlib, json, random, re, threading, traceback
from datetime import date, datetime, timedelta
from pathlib import Path

import reup, wording
from edition import WORK, load_edition

demo = Path(__file__).resolve().parent
CONFIG = demo / 'config.json'
DAILY = demo.parent / 'output' / 'daily'
REUP_OUTPUT = demo.parent / 'output' / 'reup'
REUP_WORK = WORK / 'reup'
CAPTION_LIMIT = 2200  # autotok refuses a longer caption
CAPTION_PROMPT = """Bạn chạy một trang tin TikTok tiếng Việt và đang tự gõ caption cho video {title}.

Các tin trong video:
{stories}

Viết caption như một biên tập viên viết tay, giọng bình thường, không làm màu:
- 1 hoặc 2 câu ngắn, tổng không quá {most} ký tự. Đúng chính tả: viết hoa đầu câu và tên riêng (Pháp, Aer Lingus, Lào Cai), kết thúc câu bằng dấu chấm. Không dùng tiếng lóng.
- Nói thẳng vào tin đáng chú ý nhất; nhắc thêm một tin khác nếu thấy tự nhiên. Không cần kể hết.
- Chỉ dùng thông tin ở trên và giữ đúng nghĩa từng chi tiết, không đổi cách gọi (ví dụ "nộp khắc phục hậu quả" không thành "nộp lại"). Không thêm tên, số liệu, tội danh hay nhận định. Không nối các chi tiết thành quan hệ nguyên nhân ("giúp", "khiến", "do") nếu tin không nói vậy. Con số lấy trọn và đúng đơn vị, không viết kiểu "10 trên 50".
- Nếu nhắc tới một vụ án, phải giữ tình trạng pháp lý có trong tin: bản án sơ thẩm thì ghi rõ "sơ thẩm", mới khởi tố thì ghi "khởi tố", không viết như đã kết luận có tội.
- Nêu sự việc kèm cơ quan hoặc nguồn ("công an khởi tố", "theo BBC"). Không nêu giá, cách thức hay nơi mua của ma túy, vũ khí, mại dâm, cờ bạc; số lượng tang vật chỉ nêu khi đó là điểm chính của tin. Không dùng từ giật gân, không mô tả thương tích; tin thương vong viết "thiệt mạng" thay cho "chết". Viết đúng chính tả, không viết né kiểu "m.a t.úy".
- Không emoji, không gạch đầu dòng, không dấu hai chấm mở đầu, không ngày tháng, không câu hỏi hay kêu gọi tương tác.
- Tránh văn mẫu: "điểm tin", "cập nhật", "đáng chú ý", "hôm nay có gì", "cùng theo dõi", "không thể bỏ lỡ", "bạn nghĩ sao", "tâm điểm", "gây xôn xao".
{correction}
Chỉ trả về JSON: {{"text": "...", "tags": ["tukhoa1", "tukhoa2"]}}. Mọi thông tin cần đã có ở trên: không mở tệp, không dùng công cụ nào.
tags là đúng 2 hashtag chủ đề chung, phổ biến, ngắn, tiếng Việt không dấu, đúng chính tả, viết liền, không có dấu # (ví dụ phapluat, chungkhoan, tygia, quocte, batdongsan). Không dùng tội danh hay tên người làm hashtag.
"""
CAPTION_MOST = 180
TEMPLATE_WORDS = ('điểm tin', 'cập nhật', 'đáng chú ý', 'hôm nay có gì', 'theo dõi', 'bỏ lỡ', 'bạn nghĩ', 'tâm điểm', 'xôn xao', 'nổi bật')
EMOJI = re.compile('[\U0001F300-\U0001FAFF☀-➿️]')
DAILY_LIMIT = None  # most automatic posts a day; None: no cap, only the gap and STOP_HOUR space them out
GAP_MINUTES = (90, 120)  # between automatic posts, a new random gap each time
RETRY_MINUTES = (30, 45)  # before trying again after a failure
RETRIES = 2  # further tries for a video TikTok refused
SETTLE_MINUTES = 30  # the sheet often changes soon after a render: wait this long with no new render
STOP_HOUR = 23  # nothing goes out by itself from this hour; the next morning the news is old
FIRST = 'tong-hop'  # always the day's first automatic post
WAKE_HOUR, RENDER_END_HOUR = 7, 21  # the auto_render window
STALE_HOURS = 6  # a hot-news video not posted this long after its scan is old news: it is left out
REUP_GAP = 30  # minutes between a reup sent to the bot and the post before it, of any kind (posts close together got refused)
BATCH_ID = re.compile(r'^(tin-nong|nong)-(\d{2})(\d{2})(?:-\d+)?$')


class PostError(Exception):
    """The post failed. `retry` is False when it may have gone out anyway: check TikTok before posting again.
    `stop` means no video can go out until the owner fixes something (usually logging in again)."""

    def __init__(self, message, retry=True, stop=False):
        super().__init__(message)
        self.retry = retry
        self.stop = stop


def caption(script):
    return wording.mask(script.get('caption') or ' '.join(script['title']))[:CAPTION_LIMIT]


def read_history(path):
    return json.loads(path.read_text(encoding='utf8')) if path.exists() else {}


def remember(path, key, **entry):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**read_history(path), key: {**entry, 'at': datetime.now().isoformat(timespec='seconds')}}), encoding='utf8')


def explain(e):
    """A message saying what to do about one of autotok's errors."""
    import autotok.errors as errors
    if isinstance(e, (errors.AccountNotFoundError, errors.NotLoggedInError)):
        return f'Chưa đăng nhập TikTok hoặc phiên đã hết hạn. Chạy lại lệnh autotok login trên máy. ({e})'
    if isinstance(e, errors.BrowserNotInstalledError):
        return 'Thiếu trình duyệt để ký video. Chạy lệnh autotok install-browser trên máy.'
    if isinstance(e, errors.PublishError):
        return f'TikTok từ chối bài đăng: {e}'
    return f'Không đăng được: {e}'


def post(video, text, account, history, key, private=False, ai_label=False):
    """Publish `video` and return TikTok's video id. `key` names this version in the `history` file: it never goes out twice."""
    try:
        import autotok
    except ImportError:
        raise PostError('Chưa cài autotok. Chạy: uv pip install --python .venv-demo -r demo/requirements-demo.txt', stop=True)
    if not video.exists():
        raise PostError(f'Không còn file video: {video}', retry=False)
    key = f"{'private' if private else 'public'}:{key}"
    done = read_history(history).get(key)
    if done:
        raise PostError('Bản này đã đăng rồi.' if done.get('video_id') else 'Lần đăng trước chưa rõ kết quả, hãy kiểm tra trên TikTok.', retry=False)
    try:
        result = autotok.Client.from_account(account).upload(video, text, visibility='private' if private else 'public', ai_label=ai_label)
    except autotok.errors.PublishUncertainError as e:
        remember(history, key, uncertain=True)
        raise PostError(f'Chưa rõ video đã lên chưa, hãy kiểm tra trên TikTok rồi mới đăng lại. ({e})', retry=False)
    except autotok.errors.AutotokError as e:
        blocking = (autotok.errors.AccountNotFoundError, autotok.errors.NotLoggedInError, autotok.errors.BrowserNotInstalledError)
        raise PostError(explain(e), stop=isinstance(e, blocking))
    except Exception as e:  # not one of autotok's own errors, so nothing says the post did not go out
        remember(history, key, uncertain=True)
        raise PostError(f'Lỗi không mong đợi, hãy kiểm tra trên TikTok rồi mới đăng lại. ({e})', retry=False)
    remember(history, key, video_id=result.video_id)
    return result.video_id


def post_reup(wd, video, account, private=False, ai_label=False):
    """A reup video with the caption in wd/script.json. A new render of the same link is a new video."""
    wd, video = Path(wd), Path(video)
    script = json.loads((wd / 'script.json').read_text(encoding='utf8'))
    key = video.stat().st_mtime_ns if video.exists() else ''
    text = read_history(wd / 'tiktok-captions.json').get(str(key)) or caption(script)
    return post(video, text, account, wd / 'tiktok.json', key, private, ai_label)


def reup_videos(limit=12):
    """The newest finished reup videos, for Studio."""
    found = []
    for video in sorted(REUP_OUTPUT.glob('*.mp4'), key=lambda p: p.stat().st_mtime, reverse=True) if REUP_OUTPUT.exists() else []:
        script_file = REUP_WORK / video.stem / 'script.json'
        if script_file.exists():
            script, key = json.loads(script_file.read_text(encoding='utf8')), str(video.stat().st_mtime_ns)
            captions = REUP_WORK / video.stem / 'tiktok-captions.json'
            found.append({'kind': 'reup', 'id': video.stem, 'title': ' '.join(script['title']), 'video': video,
                          'caption': read_history(captions).get(key) or caption(script), 'captions': captions,
                          'key': key, 'history': REUP_WORK / video.stem / 'tiktok.json', 'ref': key})
        if len(found) == limit:
            break
    return found


# --- news videos -----------------------------------------------------------------------------------

def news_caption(edition):
    """A plain caption from the first headline, used until (or if) Claude has not written one."""
    stories = edition['stories']
    text = wording.soften(' '.join(stories[0]['title'])) if stories else edition['title']
    if wording.money_near_barred(text):
        text = edition['title'].capitalize()
    elif len(stories) > 1:
        text += f' và {len(stories) - 1} tin khác'
    programme = edition.get('programme', '')
    tags = ['tintuc'] + (['tinnong'] if batch_of(programme) else [programme.replace('-', '')] if programme and programme != FIRST else [])
    return compose_caption(text, tags, edition)


def compose_caption(text, tags, edition):
    sources = dict.fromkeys(s['source'].split('•')[0].strip() for s in edition['stories'] if s.get('source'))
    lines = [wording.mask(text).strip(), '']
    if sources:
        lines += ['Nguồn: ' + ', '.join(sources), '']
    return '\n'.join([*lines, ' '.join('#' + t for t in tags)])[:CAPTION_LIMIT]


def caption_problems(text, edition):
    """What is wrong with Claude's text, in words it can act on; empty when it may go out."""
    found = []
    if not text or len(text) > CAPTION_MOST:
        found.append(f'Phải có chữ và không quá {CAPTION_MOST} ký tự.')
    if '\n' in text or '#' in text or re.search(r'^\s*[-•*]', text) or '—' in text:
        found.append('Chỉ viết câu liền nhau, không xuống dòng, không hashtag trong câu, không gạch đầu dòng, không gạch dài.')
    if EMOJI.search(text):
        found.append('Bỏ emoji.')
    if '?' in text or '!' in text:
        found.append('Không dùng câu hỏi hay dấu chấm than.')
    used = [w for w in TEMPLATE_WORDS if w in text.lower()]
    if used:
        found.append(f"Bỏ các cụm văn mẫu: {', '.join(used)}.")
    if sum(w.isupper() for w in re.findall(r'[^\W\d_]{2,}', text)) >= 4:
        found.append('Viết hoa thường bình thường, không viết hoa cả câu.')
    if text and text[0].islower():
        found.append('Viết hoa chữ đầu câu và tên riêng.')
    facts = json.dumps([[s.get(k) for k in ('title', 'detail', 'number', 'unit', 'note')] for s in edition['stories']], ensure_ascii=False)
    invented = [n for n in re.findall(r'\d+(?:[.,]\d+)*', text) if n not in facts]
    if invented:
        found.append(f"Các số này không có trong tin: {', '.join(invented)}. Chỉ dùng số có sẵn, viết đúng như trong tin.")
    found += wording.problems(text)
    lowered = text.lower()
    for story in edition['stories']:
        words = ' '.join(story['title']).lower().split()
        named = any(' '.join(words[i:i + 3]) in lowered for i in range(len(words) - 2))
        if named and 'sơ thẩm' in (story.get('note') or '').lower() and 'sơ thẩm' not in lowered:
            found.append(f"Tin \"{' '.join(story['title'])}\" là án sơ thẩm: phải ghi rõ \"sơ thẩm\".")
    return found


def write_caption(edition, folder):
    """Claude's caption, or None if it could not write one that passes the checks in two tries."""
    stories = '\n'.join(f"- {' '.join(s['title'])}. " + '. '.join(filter(None, [*(s.get('detail') or []), s.get('note')])) for s in edition['stories'])
    correction = ''
    for _ in range(2):
        answer = reup.ask_claude(CAPTION_PROMPT.format(title=edition['title'].lower(), stories=stories, most=CAPTION_MOST, correction=correction), folder)
        text = str(answer.get('text', '')).strip()
        wrong = caption_problems(text, edition)
        if not wrong:
            words = [re.sub(r'[^a-z0-9]', '', str(t).lower()) for t in answer.get('tags', [])]
            return compose_caption(text, ['tintuc', *[w for w in words if w and w != 'tintuc'][:2]], edition)
        correction = f'\nBản trước: "{text}". Cần sửa: {" ".join(wrong)}\n'
    return None


def news_history(day):
    return DAILY / day / 'tiktok.json'


def news_key(edition):
    """Names the content of a news video: rendering the same stories again is not a new video."""
    content = json.dumps([edition['title'], [[s.get('title'), s.get('script')] for s in edition['stories']]], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(content.encode()).hexdigest()[:8]


def render_finished(day):
    """auto_render.py writes this file after a run; re-planning the day deletes it, so a render under way has none."""
    state = WORK / day / 'auto-render.json'
    return state.exists() and json.loads(state.read_text(encoding='utf8')).get('ok', False)


def news_videos(day):
    """The day's rendered videos. One older than its news.json has stories that are not in the video yet, so it is left out."""
    plan = WORK / day / 'plan.json'
    if not plan.exists():
        return []
    found, written = [], read_history(caption_file(day))
    for programme in json.loads(plan.read_text(encoding='utf8'))['programmes']:
        news, video = WORK / day / programme['id'] / 'news.json', DAILY / day / f"{programme['id']}.mp4"
        if programme['status'] == 'ready' and news.exists() and video.exists() and video.stat().st_mtime >= news.stat().st_mtime:
            edition = load_edition(news)
            key = news_key(edition)
            found.append({'kind': 'news', 'id': programme['id'], 'day': day, 'title': edition['title'], 'video': video,
                          'preview': DAILY / day / f"{programme['id']}-contact-sheet.jpg", 'edition': edition, 'key': key,
                          'caption': written.get(f"{programme['id']}:{key}") or news_caption(edition), 'captions': caption_file(day),
                          'history': news_history(day), 'ref': f"{programme['id']}:{key}"})
    return found


def caption_file(day):
    return DAILY / day / 'tiktok-captions.json'


no_caption = set()  # versions Claude could not write for since the bot started: the plain caption goes out


def prepare_caption(day, pid):
    """Have Claude write the caption of one video, once per version. Never raises: a post must not wait on a caption."""
    item = next((v for v in news_videos(day) if v['id'] == pid), None)
    if not item or item['ref'] in no_caption or item['ref'] in read_history(caption_file(day)):
        return
    try:
        text = write_caption(item['edition'], item['video'].parent)
    except Exception:
        print(traceback.format_exc(), flush=True)
        text = None
    if not text:
        no_caption.add(item['ref'])
        return
    save_caption(item, text)


def save_caption(item, text):
    """A caption from Claude or edited by hand; it holds for this version of the video, and goes out instead of the prepared one."""
    with auto_lock:
        path = item['captions']
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps({**read_history(path), item['ref']: text}, ensure_ascii=False), encoding='utf8')
        temporary.replace(path)


def prepare_next_caption(day, videos, limit=None):
    """One caption a check, in posting order and only for videos that will go out today, so they can be read in Studio first."""
    written, names = read_history(caption_file(day)), {v['id']: v for v in videos}
    for row in auto_view(day, videos, limit)['rows']:
        item = names.get(row['id'])
        if row['state'] == 'waiting' and item and item['ref'] not in written and item['ref'] not in no_caption:
            return prepare_caption(day, row['id'])


def seen(item, kind):
    """Has this version been offered ('offered') or sent out ('public') already?"""
    return f"{kind}:{item['ref']}" in read_history(item['history'])


def mark_offered(item):
    remember(item['history'], f"offered:{item['ref']}")


def post_item(item, account, private=False, ai_label=False, text=None):
    """`text` replaces the prepared caption (Studio lets it be edited)."""
    return post(item['video'], text or item['caption'], account, item['history'], item['ref'], private, ai_label)


post_news = post_item



def status(item):
    history = read_history(item['history'])
    return {kind: history.get(f"{kind}:{item['ref']}") for kind in ('public', 'private')}


def forget_uncertain(item):
    """The owner looked on TikTok and the video is not there: it may be posted again."""
    history = read_history(item['history'])
    for kind in ('public', 'private'):
        if history.get(f"{kind}:{item['ref']}", {}).get('uncertain'):
            del history[f"{kind}:{item['ref']}"]
    item['history'].write_text(json.dumps(history), encoding='utf8')


# --- the day's automatic posts ----------------------------------------------------------------------
# output/daily/<day>/tiktok-auto.json keeps the order, the time of the next post and the tries, so a restart
# of the bot or of the machine carries on. Each programme goes out at most once a day, in its latest version.

auto_lock = threading.RLock()  # the watcher and the job thread both change the file


def auto_file(day):
    return DAILY / day / 'tiktok-auto.json'


def read_auto(day):
    return read_history(auto_file(day))


def save_auto(day, state):
    path = auto_file(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding='utf8')
    temporary.replace(path)


def later(now, span):
    return (now + timedelta(minutes=random.uniform(*span))).isoformat(timespec='seconds')


def daily_limit(start, day):
    """Most posts allowed on `day`; None means no cap."""
    return DAILY_LIMIT


def posts_today(day):
    """Public posts of that day, news and reup, by hand too."""
    histories = [news_history(day), *REUP_WORK.glob('*/tiktok.json')]
    return sum(key.startswith('public:') and entry['at'][:10] == day for path in histories for key, entry in read_history(path).items())


def posted_programmes(day):
    """Programme id -> its public post, including one that may or may not have gone out."""
    return {key.split(':')[1]: entry for key, entry in read_history(news_history(day)).items() if key.startswith('public:')}


def last_post(day):
    """The latest post of any kind, by hand too: a reup video posted by hand pushes the next news video back."""
    histories = [news_history(day), *REUP_WORK.glob('*/tiktok.json')]
    times = [datetime.fromisoformat(entry['at']) for path in histories for key, entry in read_history(path).items()
             if key.startswith(('public:', 'private:'))]
    return max(times, default=None)


# --- reups sent to the bot: one queue, a slot each, news first ----------------------------------------

def reup_queue_file():
    return WORK / 'reup-queue.json'


def read_reup_queue():
    path = reup_queue_file()
    return json.loads(path.read_text(encoding='utf8')) if path.exists() else {'queue': []}


def save_reup_queue(state):
    path = reup_queue_file()
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding='utf8')
    temporary.replace(path)


def posting_hours(when):
    """`when`, or the first moment after it inside the posting hours (WAKE_HOUR until STOP_HOUR)."""
    if when.hour >= STOP_HOUR:
        when = datetime.combine(when.date() + timedelta(days=1), datetime.min.time())
    return when.replace(hour=WAKE_HOUR, minute=0, second=0, microsecond=0) if when.hour < WAKE_HOUR else when


def book_reup(name, chat, now=None):
    """Put a finished reup in the queue and return its expected post time: REUP_GAP after the last post and
    after the last booked reup, inside the posting hours. A hot-news video waiting at that time goes first."""
    now = now or datetime.now()
    gap = timedelta(minutes=REUP_GAP)
    with auto_lock:
        state = read_reup_queue()
        state['queue'] = [e for e in state['queue'] if e['name'] != name]
        last = last_post(now.date().isoformat())
        times = [now, last + gap if last else None,
                 *(datetime.fromisoformat(e['at']) + gap for e in state['queue'][-1:])]
        at = posting_hours(max(t for t in times if t))
        state['queue'].append({'name': name, 'chat': chat, 'at': at.isoformat(timespec='seconds')})
        save_reup_queue(state)
        return at


def news_waiting(day, now):
    """Is a news video waiting to go out by itself today? Reups wait for it: each post pushes the next news video back."""
    conf = reup.settings()
    if not (conf['tiktok_auto'] and conf['tiktok_account'] and conf['tiktok_auto_from'] and day >= conf['tiktok_auto_from']):
        return False
    return render_finished(day) and bool(waiting_videos(day, news_videos(day), read_auto(day), now))


def due_reup(now=None):
    """The queued reup to post now, taken off the queue, or None."""
    now = now or datetime.now()
    gap, day = timedelta(minutes=REUP_GAP), now.date().isoformat()
    with auto_lock:
        state = read_reup_queue()
        if not state['queue'] or posting_hours(now) != now or now < datetime.fromisoformat(state['queue'][0]['at']):
            return None
        last = max(filter(None, [last_post(day), datetime.fromisoformat(state['sent']) if state.get('sent') else None]), default=None)
        if (last and now < last + gap) or news_waiting(day, now):
            return None
        entry = state['queue'].pop(0)
        state['sent'] = now.isoformat(timespec='seconds')  # its post may wait behind a render: the next one counts from here
        save_reup_queue(state)
        return entry


def reups_waiting(now=None):
    now = now or datetime.now()
    return posting_hours(now) == now and bool(read_reup_queue()['queue'])


def settled_at(day):
    """When the day's videos count as final: some time after the last finished render. None while there is no finished render."""
    return datetime.fromtimestamp((WORK / day / 'auto-render.json').stat().st_mtime) + timedelta(minutes=SETTLE_MINUTES) if render_finished(day) else None


def ready_at(day, state):
    """The earliest time the next automatic post may go out, or None while the day's render is not finished."""
    settled = settled_at(day)
    if not settled:
        return None
    last = last_post(day)
    times = [settled, last + timedelta(minutes=GAP_MINUTES[0]) if last else None,
             datetime.fromisoformat(state['next_at']) if state.get('next_at') else None]
    return max(t for t in times if t)


def batch_of(pid):
    """(is it a very hot story's own video, minutes after midnight of its scan) for a hot-news video, else None."""
    found = BATCH_ID.match(pid or '')
    return (found[1] == 'nong', int(found[2]) * 60 + int(found[3])) if found else None


def stale(pid, now=None):
    """A hot-news video whose scan is more than STALE_HOURS old."""
    batch, now = batch_of(pid), now or datetime.now()
    return bool(batch) and now.hour * 60 + now.minute - batch[1] > STALE_HOURS * 60


def waiting_videos(day, videos, state, now=None):
    done, failed, skipped = posted_programmes(day), state.get('given_up', {}), state.get('skipped', [])
    return [v for v in videos if v['id'] not in done and v['id'] not in failed and v['id'] not in skipped and not stale(v['id'], now)]


def build_order(state, videos):
    """The round-up first, then the rest in an order drawn once for the day. Hot-news videos instead go by news value:
    a very hot story's own video first, then the newest scan's video."""
    order = state.get('order', [])
    ids = list(dict.fromkeys(order + [v['id'] for v in videos]))
    if any(batch_of(i) for i in ids):
        return sorted(ids, key=lambda i: (0, not batch_of(i)[0], -batch_of(i)[1]) if batch_of(i) else (1, 0, 0))
    fresh = [v['id'] for v in videos if v['id'] not in order]
    random.shuffle(fresh)
    # The round-up takes its stories from the other videos: the ones repeating most of it go last, so a day that
    # posts only a few videos does not show the same story twice.
    stories = {v['id']: [tuple(s['title']) for s in (v.get('edition') or {}).get('stories', [])] for v in videos}
    repeats = {i: sum(title in stories.get(FIRST, []) for title in titles) for i, titles in stories.items()}
    fresh.sort(key=lambda i: repeats.get(i, 0))
    return [FIRST, *[i for i in order + fresh if i != FIRST]] if FIRST in order + fresh else order + fresh


def fix_order(day, videos):
    """Draw the order as soon as a render is finished, so the videos going out can be read before the first post."""
    with auto_lock:
        state = read_auto(day)
        order = build_order(state, videos)
        if order != state.get('order'):
            save_auto(day, {**state, 'order': order})


def set_skipped(day, pid, skip):
    """Keep one video out of today's automatic posts, or put it back."""
    with auto_lock:
        state = read_auto(day)
        skipped = [i for i in state.get('skipped', []) if i != pid] + ([pid] if skip else [])
        save_auto(day, {**state, 'skipped': skipped})


def next_auto(day, videos, now=None, limit=None):
    """The video to post now, or None. Picking it books the next slot, so the next check does not pick another."""
    now = now or datetime.now()
    with auto_lock:
        state = read_auto(day)
        ready = ready_at(day, state)
        waiting = waiting_videos(day, videos, state, now)
        if now.hour >= STOP_HOUR or not ready or now < ready or not waiting or (limit is not None and posts_today(day) >= limit):
            return None
        order = build_order(state, videos)
        names = {v['id']: v for v in waiting}
        item = names[next(i for i in order if i in names)]
        tries = state.get('tries', {})
        tries[item['id']] = tries.get(item['id'], 0) + 1
        save_auto(day, {**state, 'order': order, 'tries': tries, 'next_at': later(now, GAP_MINUTES)})
        return item


def auto_failed(day, pid, error, now=None):
    """Note a failed automatic post and say what happens next: 'stop', 'retry', 'skip' or 'check'."""
    now = now or datetime.now()
    with auto_lock:
        state = read_auto(day)
        tries = state.get('tries', {})
        given_up = state.get('given_up', {})
        if error.stop:  # no video can go out: this one keeps its tries and waits for the owner
            tries[pid] = max(tries.get(pid, 1) - 1, 0)
            outcome = 'stop'
        elif not error.retry:  # it may be on TikTok already: never again by itself, and the booked gap stands
            given_up[pid] = str(error)
            save_auto(day, {**state, 'tries': tries, 'given_up': given_up})
            return 'check'
        elif tries.get(pid, 1) <= RETRIES:
            outcome = 'retry'
        else:
            given_up[pid] = str(error)
            outcome = 'skip'
        save_auto(day, {**state, 'tries': tries, 'given_up': given_up, 'next_at': later(now, RETRY_MINUTES)})
        return outcome


def machine_needed(day, videos, limit, now=None):
    """Must the machine stay awake? From the first render hour until the day's posts are done or the last hour has come."""
    now = now or datetime.now()
    if not WAKE_HOUR <= now.hour < STOP_HOUR:
        return False
    if limit is not None and posts_today(day) >= limit:
        return False  # nothing more goes out today
    if now.hour < RENDER_END_HOUR and (not render_finished(day) or hot_news(videos)):
        return True  # a render is coming, or later scans of the day bring new hot-news videos
    return render_finished(day) and bool(waiting_videos(day, videos, read_auto(day), now))


def hot_news(videos):
    """Is today's run the hot-news kind, whose scans come through the day?"""
    try:
        if json.loads(CONFIG.read_text(encoding='utf8')).get('hot_batches'):
            return True
    except (OSError, ValueError):
        pass
    return any(batch_of(v['id']) for v in videos)


def warn_once(day, reason):
    """True the first time a reason comes up in a day, so a blocked schedule sends one message and not one per check."""
    with auto_lock:
        state = read_auto(day)
        if reason in state.get('warned', []):
            return False
        save_auto(day, {**state, 'warned': [*state.get('warned', []), reason]})
        return True


def leftovers(day, videos, now=None, limit=None):
    """Once the last hour has come or the day's limit is reached, what will not go out today."""
    now = now or datetime.now()
    over = now.hour >= STOP_HOUR or (limit is not None and posts_today(day) >= limit)
    return [v['id'] for v in waiting_videos(day, videos, read_auto(day))] if over else []


def auto_view(day, videos, limit=None):
    """Today's schedule for Studio."""
    state, done = read_auto(day), posted_programmes(day)
    failed, skipped = state.get('given_up', {}), state.get('skipped', [])
    known = state.get('order') or sorted((v['id'] for v in videos), key=lambda i: i != FIRST)
    present = {v['id'] for v in videos}
    count = posts_today(day)
    slots = None if limit is None else max(limit - count, 0)  # the first this many waiting videos will go out today
    rows, going = [], 0
    for i in known:
        if i in done:
            status = 'posted' if done[i].get('video_id') else 'check'
        elif i in failed:
            status = 'check' if 'kiểm tra' in failed[i] else 'failed'
        elif i in skipped:
            status = 'skipped'
        elif i not in present:
            continue  # no finished video of this programme
        elif stale(i):
            status = 'limited'  # old news by now: it will not go out
        else:
            going += 1
            status = 'limited' if slots is not None and going > slots else 'waiting'
        rows.append({'id': i, 'state': status, 'at': done.get(i, {}).get('at'), 'message': failed.get(i)})
    next_id = next((r['id'] for r in rows if r['state'] == 'waiting'), None)
    ready = ready_at(day, state) if next_id else None
    return {'rows': rows, 'next_at': ready.isoformat(timespec='seconds') if ready else None, 'next_id': next_id,
            'ordered': bool(state.get('order')), 'limit': limit, 'count': count, 'finished': render_finished(day)}


def channel_name(account):
    """The channel behind a saved session, so the owner can see which account will be posted to; None if TikTok does not say."""
    import autotok, requests
    saved = autotok.AccountStore().load(account)
    try:
        answer = requests.get('https://www.tiktok.com/passport/web/account/info/?aid=1459', timeout=15,
                              cookies={c['name']: c['value'] for c in saved.cookies},
                              headers={'user-agent': saved.user_agent or 'Mozilla/5.0', 'referer': 'https://www.tiktok.com/'}).json()
        return (answer.get('data') or {}).get('username') or None
    except (requests.RequestException, ValueError):
        return None


# --- accounts, for Studio ---------------------------------------------------------------------------

def accounts():
    """Names of the logged-in accounts, or None when autotok is not installed."""
    try:
        import autotok
    except ImportError:
        return None
    return autotok.AccountStore().list()


def save_settings(account, ai_label, auto):
    config = json.loads(CONFIG.read_text(encoding='utf8')) if CONFIG.exists() else {}
    config.setdefault('reup', {}).update(tiktok_account=account, tiktok_ai_label=bool(ai_label), tiktok_auto=bool(auto))
    temporary = CONFIG.with_suffix('.tmp')
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    temporary.replace(CONFIG)

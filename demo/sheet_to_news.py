"""Read approved stories for one day from the Google Sheet and plan the day's videos.

Writes demo/work/<date>/<programme>/news.json for every programme with enough stories
(one per sector plus the "hot" round-up) and demo/work/<date>/plan.json describing all of them.

Sources, in order of preference:
  --csv FILE                       a local CSV export (tests, offline runs)
  service account (config.json)    private sheet shared with the service account email
  link export (config.json)        sheet shared as "anyone with the link can view"
"""
import argparse, csv, io, json, os, re, shutil, ssl, sys
from datetime import date as Date, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from edition import WORK, default_programmes, normalise, save_edition, spoken_date
import wording

root = Path(__file__).resolve().parent
CONFIG_FILE = Path(os.environ.get('DIEMTIN_CONFIG', root / 'config.json'))

REQUIRED = ['nganh', 'chuyen_muc', 'tieu_de_1', 'nguon', 'url', 'kich_ban']  # so_lieu, don_vi: a story may have no figure worth a box
APPROVED = {'da_duyet', 'duyet', 'ok', 'approved'}
TRUE = {'true', 'x', 'co', '1', 'yes', 'hot', 'v'}
NO_IMAGE = {'none', 'khong', 'khong_co', 'no'}
TRACKING = re.compile(r'utm_.*|fbclid|gclid|zarsrc|gidzl|ref|source|from|cmpid', re.I)
EVENT_DAYS = 2      # a story repeating an event of the last two days is dropped
EVENT_OVERLAP = .4  # share of headline words two rows about the same event have in common
# Longest text that still fits its slot on the 1080px card (Arial, sizes in prepare.py).
LIMITS = {'chuyen_muc': 34, 'tieu_de_1': 26, 'tieu_de_2': 26, 'so_lieu': 9, 'don_vi': 40,
          'chi_tiet_1': 42, 'chi_tiet_2': 42, 'ghi_chu': 110, 'nguon': 55, 'kich_ban': 450}


class SheetError(Exception):
    pass


def parse_date(value, default_year):
    value = str(value).strip()
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y'):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    m = re.fullmatch(r'(\d{1,2})/(\d{1,2})', value)
    if m:
        return Date(default_year, int(m[2]), int(m[1]))
    return None


def parse_datetime(value):
    """Publication time as written by the routine or by Google Sheets' own date formatting."""
    value = re.sub(r'\s+', ' ', str(value).strip())
    for fmt in ('%d/%m/%Y %H:%M', '%d/%m/%Y %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d %H:%M:%S',
                '%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%M:%S', '%H:%M %d/%m/%Y'):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    return None


def freshness_cutoff(day, freshness):
    """Oldest accepted publication time: `max_age_hours` before `anchor_hour` on the edition day."""
    freshness = freshness or {}
    anchor = datetime.combine(day, datetime.min.time()) + timedelta(hours=freshness.get('anchor_hour', 7))
    return anchor - timedelta(hours=freshness.get('max_age_hours', 14))


def canonical_url(url):
    """Same article whatever the scheme, www./m. prefix, trailing slash, fragment or tracking parameters."""
    parts = urlsplit(url.strip())
    host = re.sub(r'^(www|m)\.', '', parts.netloc.lower())
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if not TRACKING.fullmatch(k)))
    return host + parts.path.rstrip('/') + ('?' + query if query else '')


def event_key(rec):
    """The figure and headline words of a row; two outlets reporting one event share the figure."""
    words = set(normalise(rec.get('tieu_de_1', '') + ' ' + rec.get('tieu_de_2', '')).split('_')) - {''}
    return re.sub(r'\s+', '', rec.get('so_lieu', '')).lower(), normalise(rec.get('don_vi', '')), words


def same_event(a, b):
    (num_a, unit_a, words_a), (num_b, unit_b, words_b) = a, b
    if not num_a or num_a != num_b or unit_a != unit_b or not words_a or not words_b:
        return False
    return len(words_a & words_b) / len(words_a | words_b) >= EVENT_OVERLAP


def rows_to_dicts(rows):
    rows = [r for r in rows if any(str(c).strip() for c in r)]
    if not rows:
        raise SheetError('Sheet trống')
    header = [normalise(h) for h in rows[0]]
    missing = [c for c in REQUIRED + ['ngay', 'trang_thai'] if c not in header]
    if missing:
        raise SheetError('Thiếu cột: ' + ', '.join(missing) + ' (import lại sheet-template.csv mới)')
    return [{h: str(r[i]).strip() if i < len(r) else '' for i, h in enumerate(header) if h} for r in rows[1:]]


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float('inf')


OUTLETS = ['VnExpress', 'CafeF', 'Tuổi Trẻ', 'Thanh Niên', 'VnEconomy', 'Dân trí', 'Báo Chính phủ', 'Pháp Luật TP HCM', 'Pháp Luật',
           'BBC', 'Reuters', 'AP', 'Associated Press', 'AFP', 'Al Jazeera', 'Deutsche Welle', 'DW', 'The Guardian', 'Guardian',
           'New York Times', 'The New York Times', 'NYT', 'France 24', 'South China Morning Post', 'SCMP', 'NPR', 'ABC News', 'CNBC', 'CNN']


def unattributed(script, source):
    """The script without "Theo <outlet>," opening a sentence: the card already names the outlet, and saying it in every
    story costs a second each. "Theo Bộ Công an," stays: that names who said it, not where it was read."""
    named = [name.strip() for name in re.split(r'[,;/]| và ', source.split(' • ')[0]) if name.strip()]
    outlets = [re.escape(name) for name in sorted(set(named + OUTLETS), key=len, reverse=True)]  # "DW" in the source, "Deutsche Welle" read out
    cut = re.compile(r'(^|(?<=[.!?…] ))Theo (?:báo |tờ |hãng tin |đài )?(?:' + '|'.join(outlets) + r')(?:, dẫn (?:lại |nguồn |tin )?[^,.]{2,40})?, (\w)', re.I)
    return cut.sub(lambda m: m[1] + m[2].upper(), script)


def figure_of(number, unit):
    """The card's big number and its label, or ('', '') when the number is not the story's: someone's age, or no label.
    "tuổi, tuổi của quân nhân" (the unit written twice) loses the first one."""
    unit = re.sub(r'^(.{2,20}?)\s*,\s*(?=\1\b)', '', unit.strip(), flags=re.I)
    version = re.fullmatch(r'\d+(?:\.\d+){2,}', number.strip()) and not re.fullmatch(r'\d{1,3}(?:\.\d{3})+', number.strip())
    if not number.strip() or not unit or normalise(unit).startswith('tuoi') or version:  # "27.2.1" is an iOS release, not a quantity
        return '', ''
    return number.strip(), unit


def same_edition(path, edition):
    """Is the news.json at `path` this very edition? Then its folder (photos, voice, cards) and its video still stand."""
    try:
        return json.loads(Path(path).read_text()) == json.loads(json.dumps(edition))
    except (OSError, ValueError):
        return False


def approved_stories(records, day, freshness=None):
    """Approved, valid rows for `day` as stories, plus warnings and errors.

    A row with a missing required field, a publication time older than the freshness window, or a URL
    already used on an earlier day is left out (and reported); the other rows still make videos.
    The freshness rule applies once the sheet has a "Thời gian đăng" column."""
    stories, warnings, errors = [], [], []
    check_time = any('thoi_gian_dang' in rec for rec in records)
    enforce_from = (freshness or {}).get('enforce_from')  # rows of earlier days predate the column
    if enforce_from and day < Date.fromisoformat(enforce_from):
        check_time = False
    cutoff = freshness_cutoff(day, freshness)
    used_before, recent_events = {}, []
    for n, rec in enumerate(records, start=2):  # URL -> first earlier day it appeared, so a story never airs twice
        d = parse_date(rec.get('ngay', ''), day.year)
        if d and d < day and rec.get('url'):
            used_before.setdefault(canonical_url(rec['url']), d)
            if (day - d).days <= EVENT_DAYS and normalise(rec.get('trang_thai', '')) in APPROVED:
                recent_events.append((f'dòng {n} ngày {d:%d/%m}', event_key(rec)))
    seen_today = set()
    for n, rec in enumerate(records, start=2):  # row 1 is the header
        if parse_date(rec.get('ngay', ''), day.year) != day or normalise(rec.get('trang_thai', '')) not in APPROVED:
            continue
        label = f'Dòng {n}'
        missing = [col for col in REQUIRED if not rec.get(col)]
        if missing:
            errors.append(f'{label}: thiếu {", ".join(missing)}; bỏ qua tin này')
            continue
        url = canonical_url(rec['url'])
        if url in used_before:
            errors.append(f'{label}: tin đã dùng ngày {used_before[url]:%d/%m}; bỏ qua tin cũ')
            continue
        if url in seen_today:
            errors.append(f'{label}: trùng URL với một tin khác hôm nay; bỏ qua')
            continue
        seen_today.add(url)
        published = parse_datetime(rec.get('thoi_gian_dang', ''))
        if check_time and not published:
            errors.append(f'{label}: thiếu hoặc sai "Thời gian đăng" (DD/MM/YYYY HH:MM); bỏ qua để tránh tin cũ')
            continue
        if published and published < cutoff:
            errors.append(f'{label}: đăng lúc {published:%H:%M %d/%m}, trước mốc {cutoff:%H:%M %d/%m}; bỏ qua tin cũ')
            continue
        key = event_key(rec)
        repeat = next((where for where, other in recent_events if same_event(key, other)), None)
        if repeat:
            errors.append(f'{label}: trùng sự kiện với {repeat} (cùng số liệu, tiêu đề gần giống); bỏ qua')
            continue
        shown = ('tieu_de_1', 'tieu_de_2', 'don_vi', 'chi_tiet_1', 'chi_tiet_2', 'kich_ban')  # what the video says or shows
        barred = next((s for col in shown for s in wording.money_near_barred(rec.get(col, ''))), None)
        if barred:  # the story still airs: wording never costs a story
            warnings.append(f'{label}: có câu nêu tiền cạnh ma túy, vũ khí, mại dâm hoặc cờ bạc ("{barred[:70]}")')
        recent_events.append((f'dòng {n}', key))
        for col, limit in LIMITS.items():
            if len(rec.get(col, '')) > limit:
                warnings.append(f'{label}: "{col}" dài {len(rec[col])} ký tự (nên ≤ {limit}), có thể tràn khung')
        rec = {**rec, **{col: wording.soften(rec[col]) if col == 'kich_ban' else wording.mask(wording.soften(rec[col])) for col in shown if rec.get(col)}}  # prepare.py masks the script's captions
        if wording.problems(rec['kich_ban']):
            warnings.append(f'{label}: kịch bản còn từ "chết"; nên viết "thiệt mạng"')
        rec['kich_ban'] = unattributed(rec['kich_ban'], rec['nguon'])
        number, unit = figure_of(rec.get('so_lieu', ''), rec.get('don_vi', ''))
        if rec.get('so_lieu') and not number:
            warnings.append(f'{label}: số liệu "{rec["so_lieu"]} {rec.get("don_vi", "")}" là tuổi hoặc thiếu đơn vị, không phải con số của tin; thẻ không có ô số')
        digits = re.findall(r'[^\s,.;:!?"()]*\d[^\s,;:!?"()]*', rec['kich_ban'])
        if digits:  # named, so a road or model name ("A1", "5G") is not taken for a forgotten figure
            warnings.append(f'{label}: kịch bản còn chữ số ({", ".join(digits[:4])}); giọng đọc có thể đọc sai, nên viết bằng chữ (ví dụ "A một")')
        story = {
            'category': rec['chuyen_muc'].upper(),
            'title': [t for t in (rec['tieu_de_1'], rec.get('tieu_de_2', '')) if t],
            'number': number,
            'unit': unit.upper(),
            'detail': [t for t in (rec.get('chi_tiet_1', ''), rec.get('chi_tiet_2', '')) if t],
            'note': rec.get('ghi_chu', ''),
            'source': rec['nguon'],
            'url': rec['url'],
            'script': rec['kich_ban'],
            'sector': normalise(rec['nganh']),  # picks the fallback photo (fetch_images.stock_photo)
        }
        image = rec.get('anh_url', '')
        if normalise(image) in NO_IMAGE or image.strip() == '-':
            story['image'] = False
        elif image:
            story['image'] = image
        if rec.get('anh_nguon'):
            story['image_credit'] = rec['anh_nguon']
        if rec.get('tu_khoa_anh', '').strip():  # English words to search a free photo with (fetch_images.found_photo)
            story['image_query'] = rec['tu_khoa_anh'].strip()
        video = rec.get('video_url', '').strip()
        if video.startswith('http'):  # a free-licence clip (fetch_images.fetch_story_clip)
            story['video'] = video
            story['video_credit'] = rec.get('video_nguon', '')
        if published:
            story['published'] = published.strftime('%Y-%m-%d %H:%M')
        stories.append({'story': story, 'row': n, 'sector': normalise(rec['nganh']), 'sector_label': rec['nganh'],
                        'hot': normalise(rec.get('hot', '')) in TRUE,
                        'very_hot': normalise(rec.get('ly_do_nong', '')).startswith('cuc_nong'),  # the routine's mark: a video of its own
                        'order': _number(rec.get('thu_tu')), 'hot_order': _number(rec.get('thu_tu_hot'))})
    return stories, warnings, errors


def programme_sectors(prog):
    """The sectors a video takes its stories from: `nganh` is one name, or a list when it merges several."""
    names = prog.get('filter', {}).get('nganh', '')
    return {normalise(name) for name in ([names] if isinstance(names, str) else names)}


def plan_programmes(stories, programmes, day, config=None):
    """Assign stories to programmes. Returns [(programme, edition | None, reason)]."""
    config = config or {}
    plans = []
    for prog in programmes:
        f = prog.get('filter', {})
        if f.get('urls'):  # a hot-news batch: the stories one scan found
            wanted = set(f['urls'])
            picked = sorted((s for s in stories if canonical_url(s['story']['url']) in wanted), key=lambda s: (s['hot_order'], s['order'], s['row']))
        elif f.get('hot'):
            picked = sorted((s for s in stories if s['hot']), key=lambda s: (s['hot_order'], s['order'], s['row']))
        else:
            sectors = programme_sectors(prog)
            picked = sorted((s for s in stories if s['sector'] in sectors), key=lambda s: (s['order'], s['row']))
        picked = picked[:prog.get('max', 6)]
        if len(picked) < prog.get('min', 1):
            plans.append((prog, None, f'{len(picked)} tin đã duyệt, cần ít nhất {prog.get("min", 1)}'))
            continue
        edition_stories = []
        for s in picked:
            story = dict(s['story'])
            if prog.get('accent'):
                story['accent'] = prog['accent']
            edition_stories.append(story)
        intro = prog.get('intro') or prog['title'].capitalize()
        edition = {'date': day.isoformat(), 'programme': prog['id'], 'title': prog['title'],
                   # Read over the intro card, so a story shared by two videos keeps one cached recording.
                   'intro': f'{intro} hôm nay, {spoken_date(day.isoformat())}.',
                   'outro': prog.get('outro', config.get('outro', '')),
                   'intro_title': prog.get('intro_title') or [prog['title']],
                   'intro_tagline': prog.get('intro_tagline', ''),
                   'intro_accent': prog.get('intro_accent') or prog.get('accent') or '#4ADE80',
                   'brand': prog.get('brand') or config.get('brand', 'TIN TỨC KINH TẾ VIỆT NAM'),
                   'stories': edition_stories}
        if prog.get('voice'):  # otherwise the voice chosen in Studio / --voice
            edition['voice'] = prog['voice']
        if prog.get('layouts') or config.get('layouts'):  # story card layouts, used in turn
            edition['layouts'] = prog.get('layouts') or config['layouts']
        if prog.get('intro_image') or config.get('intro_image'):
            edition['intro_image'] = prog.get('intro_image') or config['intro_image']
        plans.append((prog, edition, ''))
    return plans


BATCH_MIN = 2  # hot stories one scan must bring for a video of its own; fewer wait for the next scan
LAST_BATCH_HOUR = 19  # from this hour whatever waits makes a video: no scan is coming after it
PARTS = [(11, 'SÁNG'), (14, 'TRƯA'), (18, 'CHIỀU'), (24, 'TỐI')]


def batches_file(day):
    """Which hot stories went into which batch, kept beside the day's folder: the sheet step clears that folder."""
    return WORK / f'{day.isoformat()}-dot.json'


def assign_batches(stories, day, now=None):
    """Group the day's hot stories by the scan that brought them. A story keeps its batch for the whole day, so a video
    already made (and maybe posted) is never made again with other stories. New stories make a batch once there are
    BATCH_MIN of them, or one of them is very hot, or the day's last scans are done; until then they wait."""
    now = now or datetime.now()
    path = batches_file(day)
    state = json.loads(path.read_text(encoding='utf8')) if path.exists() else {'batches': [], 'waiting': []}
    known = {u for b in state['batches'] for u in b['urls']}
    new = [s for s in stories if s['hot'] and canonical_url(s['story']['url']) not in known]
    urls = [canonical_url(s['story']['url']) for s in new]
    if new and (len(new) >= BATCH_MIN or any(s['very_hot'] for s in new) or now.hour >= LAST_BATCH_HOUR):
        bid = f'{now:%H%M}'
        while any(b['id'] == bid for b in state['batches']):
            bid = f'{int(bid) + 1:04d}'
        state['batches'].append({'id': bid, 'urls': urls})
        urls = []
    state['waiting'] = urls
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding='utf8')
    return state


def batch_programmes(state, stories, config):
    """One "Tin nóng" video per batch, and one video of its own for each very hot story (left out of its batch's video)."""
    base = next((p for p in config.get('programmes') or [] if p.get('filter', {}).get('hot')), {})
    common = {k: v for k, v in base.items() if k in ('accent', 'voice', 'layouts', 'intro_image', 'brand', 'outro', 'intro_accent')}
    by_url = {canonical_url(s['story']['url']): s for s in stories}
    progs = []
    for b in state['batches']:
        if not b['id'].isdigit():
            continue  # stories already used another way (the sector videos of the day the switch was made)
        urls = [u for u in b['urls'] if u in by_url]
        single = [u for u in urls if by_url[u]['very_hot']]
        rest = [u for u in urls if u not in single]
        part = next(name for hour, name in PARTS if int(b['id'][:2]) < hour)
        if rest:
            progs.append({**common, 'id': f'tin-nong-{b["id"]}', 'title': f'TIN NÓNG {part}', 'filter': {'urls': rest}, 'min': 1, 'max': 6})
        for k, u in enumerate(single, 1):
            progs.append({**common, 'id': f'nong-{b["id"]}-{k}', 'title': 'TIN NÓNG', 'filter': {'urls': [u]}, 'min': 1, 'max': 1})
    return progs


def unassigned_warnings(stories, programmes):
    sectors = set().union(*map(programme_sectors, programmes))
    # A hot story outside the sectors ("Thời sự") is meant for the round-up alone; only a non-hot one is lost.
    return [f'Dòng {s["row"]}: ngành "{s["sector_label"]}" không có video riêng nên tin này không vào video nào'
            for s in stories if s['sector'] not in sectors and not s['hot']]


def load_config():
    if not CONFIG_FILE.exists():
        raise SheetError(f'Chưa có {CONFIG_FILE.name}; chép demo/config.example.json thành demo/config.json')
    return json.loads(CONFIG_FILE.read_text())


def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def fetch_rows(config):
    sheet_id = config['sheet_id']
    key_file = config.get('service_account_file')
    if key_file:
        try:
            from google.oauth2 import service_account
            from google.auth.transport.requests import AuthorizedSession
        except ImportError as exc:
            raise SheetError('Cần cài: .venv-demo/bin/pip install -r demo/requirements-demo.txt') from exc
        key_path = Path(key_file) if Path(key_file).is_absolute() else root / key_file
        creds = service_account.Credentials.from_service_account_file(
            str(key_path), scopes=['https://www.googleapis.com/auth/spreadsheets.readonly'])
        tab = config.get('sheet_name', 'Tin')
        url = f'https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{quote(tab)}'
        r = AuthorizedSession(creds).get(url, params={'valueRenderOption': 'FORMATTED_VALUE'}, timeout=30)
        if r.status_code != 200:
            raise SheetError(f'Sheets API {r.status_code}: {r.text[:300]}')
        return r.json().get('values', [])
    # export?format=csv keeps every cell as text (gviz guesses column types and blanks mixed ones).
    url = f'https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={config.get("sheet_gid", 0)}'
    not_shared = SheetError('Không đọc được Sheet qua link. Kiểm tra sheet_id/sheet_gid, bật "Bất kỳ ai có '
                            'đường liên kết đều xem được" hoặc cấu hình service_account_file')
    try:
        with urlopen(Request(url, headers={'User-Agent': 'diem-tin'}), timeout=30, context=_ssl_context()) as r:
            body = r.read().decode('utf-8-sig')
            if 'text/csv' not in r.headers.get('Content-Type', ''):  # private sheets redirect to a login page
                raise not_shared
    except HTTPError as e:
        raise not_shared from e
    except URLError as e:
        raise SheetError(f'Không kết nối được Google: {e.reason}') from e
    return list(csv.reader(io.StringIO(body)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--date', default=Date.today().isoformat(), help='YYYY-MM-DD (mặc định: hôm nay)')
    parser.add_argument('--csv', help='Đọc từ file CSV thay vì Google Sheet')
    args = parser.parse_args(argv)
    day = Date.fromisoformat(args.date)
    try:
        config = load_config() if CONFIG_FILE.exists() or not args.csv else {}
        programmes = config.get('programmes') or default_programmes()
        if args.csv:
            with open(args.csv, encoding='utf-8-sig') as f:
                rows = list(csv.reader(f))
        else:
            rows = fetch_rows(config)
        stories, warnings, errors = approved_stories(rows_to_dicts(rows), day, config.get('freshness'))
    except SheetError as e:
        print('LỖI:', e, flush=True)
        return 1
    waiting = []
    if config.get('hot_batches'):  # videos follow the scans through the day, not the sectors
        state = assign_batches(stories, day)
        programmes, waiting = batch_programmes(state, stories, config), state['waiting']
    else:
        warnings += unassigned_warnings(stories, programmes)
    plans = plan_programmes(stories, programmes, day, config)

    day_dir = WORK / day.isoformat()
    editions = {prog['id']: edition for prog, edition, _ in plans if edition}
    kept = set()
    for item in day_dir.iterdir() if day_dir.exists() else []:  # stale editions from an earlier run must not be rendered again
        if item.is_dir() and item.name in editions and same_edition(item / 'news.json', editions[item.name]):
            kept.add(item.name)  # the sheet changed elsewhere: this video need not be made again (run_daily.py)
        elif item.is_dir():
            shutil.rmtree(item)
        else:  # auto-render.json too: while it is gone, the day's render counts as under way (tiktok_post.render_finished)
            item.unlink()
    summary = []
    for prog, edition, reason in plans:
        entry = {'id': prog['id'], 'title': prog['title'], 'status': 'ready' if edition else 'skipped', 'reason': reason}
        if edition:
            if prog['id'] in kept:
                entry['unchanged'] = True
            else:
                save_edition(edition, day_dir / prog['id'] / 'news.json')
            entry['count'] = len(edition['stories'])
            entry['stories'] = [' '.join(s['title']) for s in edition['stories']]
        summary.append(entry)
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / 'plan.json').write_text(json.dumps(
        {'date': day.isoformat(), 'stories': len(stories), 'programmes': summary, 'warnings': warnings, 'errors': errors},
        ensure_ascii=False, indent=2))

    for e in errors:
        print('LỖI:', e, flush=True)
    for w in warnings:
        print('CẢNH BÁO:', w, flush=True)
    ready = [p for p in summary if p['status'] == 'ready']
    print(f'{len(stories)} tin đã duyệt ngày {day:%d/%m/%Y}; {len(ready)} video sẽ tạo:', flush=True)
    for p in summary:
        print(f'  {"✓" if p["status"] == "ready" else "–"} {p["title"]}: '
              + (f'{p["count"]} tin' if p['status'] == 'ready' else f'bỏ qua ({p["reason"]})'), flush=True)
    if waiting:
        print(f'{len(waiting)} tin nóng mới đang chờ lượt quét sau để đủ một video', flush=True)
    if not ready:
        if config.get('hot_batches'):  # the first scan of the day has not brought enough yet: nothing is wrong
            print(f'Chưa có video tin nóng nào cho ngày {day:%d/%m/%Y}', flush=True)
            return 0
        print(f'LỖI: không có video nào đủ tin cho ngày {day:%d/%m/%Y}', flush=True)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

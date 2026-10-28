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
from urllib.parse import quote
from urllib.request import Request, urlopen

from edition import WORK, default_programmes, normalise, save_edition, spoken_date

root = Path(__file__).resolve().parent
CONFIG_FILE = Path(os.environ.get('DIEMTIN_CONFIG', root / 'config.json'))

REQUIRED = ['nganh', 'chuyen_muc', 'tieu_de_1', 'so_lieu', 'don_vi', 'nguon', 'url', 'kich_ban']
APPROVED = {'da_duyet', 'duyet', 'ok', 'approved'}
TRUE = {'true', 'x', 'co', '1', 'yes', 'hot', 'v'}
NO_IMAGE = {'none', 'khong', 'khong_co', 'no'}
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


def approved_stories(records, day, freshness=None):
    """Approved, valid rows for `day` as stories, plus warnings and errors.

    A row with a missing required field, a publication time older than the freshness window, or a URL
    already used on an earlier day is left out (and reported); the other rows still make videos.
    The freshness rule applies once the sheet has a "Thời gian đăng" column."""
    stories, warnings, errors = [], [], []
    check_time = any('thoi_gian_dang' in rec for rec in records)
    cutoff = freshness_cutoff(day, freshness)
    used_before = {}
    for rec in records:  # URL -> first earlier day it appeared, so a story never airs twice
        d = parse_date(rec.get('ngay', ''), day.year)
        if d and d < day and rec.get('url'):
            used_before.setdefault(rec['url'].strip(), d)
    seen_today = set()
    for n, rec in enumerate(records, start=2):  # row 1 is the header
        if parse_date(rec.get('ngay', ''), day.year) != day or normalise(rec.get('trang_thai', '')) not in APPROVED:
            continue
        label = f'Dòng {n}'
        missing = [col for col in REQUIRED if not rec.get(col)]
        if missing:
            errors.append(f'{label}: thiếu {", ".join(missing)}; bỏ qua tin này')
            continue
        url = rec['url'].strip()
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
        for col, limit in LIMITS.items():
            if len(rec.get(col, '')) > limit:
                warnings.append(f'{label}: "{col}" dài {len(rec[col])} ký tự (nên ≤ {limit}), có thể tràn khung')
        if re.search(r'\d', rec['kich_ban']):
            warnings.append(f'{label}: kịch bản còn chữ số; giọng đọc có thể đọc sai, nên viết số bằng chữ')
        story = {
            'category': rec['chuyen_muc'].upper(),
            'title': [t for t in (rec['tieu_de_1'], rec.get('tieu_de_2', '')) if t],
            'number': rec['so_lieu'],
            'unit': rec['don_vi'].upper(),
            'detail': [t for t in (rec.get('chi_tiet_1', ''), rec.get('chi_tiet_2', '')) if t],
            'note': rec.get('ghi_chu', ''),
            'source': rec['nguon'],
            'url': rec['url'],
            'script': rec['kich_ban'],
        }
        image = rec.get('anh_url', '')
        if normalise(image) in NO_IMAGE or image.strip() == '-':
            story['image'] = False
        elif image:
            story['image'] = image
        if rec.get('anh_nguon'):
            story['image_credit'] = rec['anh_nguon']
        if published:
            story['published'] = published.strftime('%Y-%m-%d %H:%M')
        stories.append({'story': story, 'row': n, 'sector': normalise(rec['nganh']), 'sector_label': rec['nganh'],
                        'hot': normalise(rec.get('hot', '')) in TRUE,
                        'order': _number(rec.get('thu_tu')), 'hot_order': _number(rec.get('thu_tu_hot'))})
    return stories, warnings, errors


def plan_programmes(stories, programmes, day, config=None):
    """Assign stories to programmes. Returns [(programme, edition | None, reason)]."""
    config = config or {}
    plans = []
    for prog in programmes:
        f = prog.get('filter', {})
        if f.get('hot'):
            picked = sorted((s for s in stories if s['hot']), key=lambda s: (s['hot_order'], s['order'], s['row']))
        else:
            sector = normalise(f.get('nganh', ''))
            picked = sorted((s for s in stories if s['sector'] == sector), key=lambda s: (s['order'], s['row']))
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
                   'intro': f'{intro} {spoken_date(day.isoformat())}. {prog.get("intro_hook", "")}'.strip(),
                   'outro': prog.get('outro', config.get('outro', '')),
                   'intro_title': prog.get('intro_title') or [prog['title']],
                   'intro_tagline': prog.get('intro_tagline', ''),
                   'intro_accent': prog.get('intro_accent') or prog.get('accent') or '#4ADE80',
                   'brand': config.get('brand', 'TIN TỨC KINH TẾ VIỆT NAM'),
                   'stories': edition_stories}
        if prog.get('voice'):  # otherwise the voice chosen in Studio / --voice
            edition['voice'] = prog['voice']
        if prog.get('intro_image') or config.get('intro_image'):
            edition['intro_image'] = prog.get('intro_image') or config['intro_image']
        plans.append((prog, edition, ''))
    return plans


def unassigned_warnings(stories, programmes):
    sectors = {normalise(p.get('filter', {}).get('nganh', '')) for p in programmes}
    return [f'Dòng {s["row"]}: ngành "{s["sector_label"]}" không có video riêng'
            + (' (vẫn vào video nổi bật)' if s['hot'] else '') for s in stories if s['sector'] not in sectors]


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
    warnings += unassigned_warnings(stories, programmes)
    plans = plan_programmes(stories, programmes, day, config)

    day_dir = WORK / day.isoformat()
    if day_dir.exists():  # stale editions from an earlier run must not be rendered again
        shutil.rmtree(day_dir)
    summary = []
    for prog, edition, reason in plans:
        entry = {'id': prog['id'], 'title': prog['title'], 'status': 'ready' if edition else 'skipped', 'reason': reason}
        if edition:
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
    if not ready:
        print(f'LỖI: không có video nào đủ tin cho ngày {day:%d/%m/%Y}', flush=True)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

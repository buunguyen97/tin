"""Edition files (one news.json per video) and the programmes that produce them."""
import json, re, unicodedata
from pathlib import Path

root = Path(__file__).resolve().parent
NEWS_FILE = root / 'news.json'  # legacy single-video edition
WORK = root / 'work'            # work/<date>/<programme>/news.json
DEFAULT_TITLE = 'ĐIỂM TIN KINH TẾ'
ACCENTS = ['#72F1C8', '#8EBAFF', '#FFD687', '#FFA9B6', '#C4AEFF', '#7FE3FF']
SECTORS = ['Vĩ mô – Chính sách', 'Tài chính – Ngân hàng', 'Chứng khoán', 'Doanh nghiệp',
           'Bất động sản', 'Năng lượng', 'Công nghệ', 'Hàng không – Du lịch', 'Pháp luật']
SECTOR_ACCENTS = ['#8EBAFF', '#72F1C8', '#FFD687', '#FFA9B6', '#C4AEFF', '#FFB86B', '#7FE3FF', '#F7A8FF', '#FF8A80']
# VieNeu v3 Turbo presets (name, description); ⭐ marks the ones the VieNeu repo recommends.
VOICES = [('Hải Đăng', 'Nam · Bắc · tự nhiên ⭐'), ('Mai Anh', 'Nữ · Bắc · tin tức ⭐'), ('Thùy Dung', 'Nữ · Nam · tin tức ⭐'),
          ('Minh Đức', 'Nam · Bắc · tin tức'), ('Minh Triết', 'Nam · Nam · tin tức'), ('Trúc Ly', 'Nữ · Bắc · tự nhiên ⭐'),
          ('Ngọc Huyền', 'Nữ · Bắc · tự nhiên ⭐'), ('Adam bựa', 'Nam · Bắc · tự nhiên ⭐'), ('Thiện Minh', 'Nam · Bắc · kể chuyện ⭐'),
          ('Thiền Tâm Đức', 'Nam · Bắc · kể chuyện ⭐'), ('Quang Sơn', 'Nam · Trung · tự nhiên ⭐'), ('Ngọc Trân', 'Nữ · Trung · tự nhiên ⭐'),
          ('Phạm Tuyên', 'Nam · Bắc · tự nhiên'), ('Xuân Vĩnh', 'Nam · Bắc · tự nhiên'), ('Quốc Tuấn', 'Nam · Bắc · tự nhiên'),
          ('Đoan Trang', 'Nữ · Bắc · tự nhiên'), ('Adam', 'Nam · Nam · tự nhiên'), ('Thanh Bình', 'Nam · Bắc · kể chuyện'),
          ('Thái Sơn', 'Nam · Nam · kể chuyện'), ('Ngọc Linh', 'Nữ · Bắc · kể chuyện'), ('Thục Đoan', 'Nữ · Nam · kể chuyện'),
          ('Mỹ Duyên', 'Nữ · Nam · đọc truyện'), ('Quỳnh Anh', 'Nữ · Bắc · đọc truyện'), ('Đức Trí', 'Nam · Nam · đọc truyện'),
          ('Kim Thanh', 'Nữ · Nam · đọc truyện')]
WEEKDAYS = ['THỨ HAI', 'THỨ BA', 'THỨ TƯ', 'THỨ NĂM', 'THỨ SÁU', 'THỨ BẢY', 'CHỦ NHẬT']
DIGITS = ['không', 'một', 'hai', 'ba', 'bốn', 'năm', 'sáu', 'bảy', 'tám', 'chín', 'mười']


def normalise(value):
    """'Bất động sản' -> 'bat_dong_san'; used for headers, sectors and status values."""
    value = unicodedata.normalize('NFD', str(value).strip().lower()).replace('đ', 'd')
    value = ''.join(c for c in value if unicodedata.category(c) != 'Mn')
    return re.sub(r'[^a-z0-9]+', '_', value).strip('_')


def slug(value):
    return normalise(value).replace('_', '-')


def default_programmes():
    """One round-up of hot stories plus one video per sector. Copy into config.json to customise."""
    progs = [{'id': 'tong-hop', 'title': 'TIN NỔI BẬT TRONG NGÀY', 'filter': {'hot': True}, 'min': 3, 'max': 6,
              'intro': 'Điểm tin nổi bật', 'intro_hook': 'Những sự kiện đáng chú ý có thể bạn đã bỏ lỡ.',
              'intro_title': ['SỰ KIỆN NỔI BẬT', 'TRONG NGÀY'], 'intro_tagline': 'CÓ THỂ BẠN ĐÃ BỎ LỠ',
              'intro_accent': '#4ADE80'}]
    for sector, accent in zip(SECTORS, SECTOR_ACCENTS):
        progs.append({'id': slug(sector), 'title': 'ĐIỂM TIN ' + sector.upper(),
                      'filter': {'nganh': sector}, 'min': 2, 'max': 5, 'accent': accent,
                      'intro': 'Điểm tin ' + sector.lower().replace(' – ', ', '),
                      'intro_hook': 'Cùng điểm qua những tin đáng chú ý nhất.',
                      'intro_title': ['ĐIỂM TIN', sector.upper()], 'intro_tagline': 'NHỮNG TIN ĐÁNG CHÚ Ý NHẤT',
                      'intro_accent': accent})
    return progs


def number_words(n):
    """Vietnamese words for 1..99, as read in dates."""
    if n <= 10:
        return DIGITS[n]
    tens, unit = divmod(n, 10)
    head = 'mười' if tens == 1 else DIGITS[tens] + ' mươi'
    if unit == 0:
        return head
    tail = {1: 'mốt' if tens > 1 else 'một', 4: 'tư' if tens > 1 else 'bốn', 5: 'lăm'}.get(unit, DIGITS[unit])
    return f'{head} {tail}'


def spoken_date(date):
    """'2026-10-05' -> 'ngày mùng năm tháng mười' (what the voice reads)."""
    _, m, d = (int(x) for x in date.split('-'))
    day = ('mùng ' if d <= 10 else '') + number_words(d)
    month = {1: 'một', 4: 'tư'}.get(m, number_words(m))
    return f'ngày {day} tháng {month}'


def load_edition(path=NEWS_FILE):
    """Return {'date', 'title', 'stories', ...}. Older files are a bare list for 27/10/2026."""
    data = json.loads(Path(path).read_text())
    if isinstance(data, list):
        data = {'date': '2026-10-27', 'stories': data}
    data.setdefault('title', DEFAULT_TITLE)
    for i, story in enumerate(data['stories']):
        story.setdefault('accent', ACCENTS[i % len(ACCENTS)])
    return data


def save_edition(edition, path=NEWS_FILE):
    # One story per line keeps diffs readable when an editor tweaks a single field.
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    stories = ',\n'.join('  ' + json.dumps(s, ensure_ascii=False, separators=(',', ':')) for s in edition['stories'])
    head = {k: v for k, v in edition.items() if k != 'stories'}
    Path(path).write_text(json.dumps(head, ensure_ascii=False)[:-1] + ',\n "stories":[\n' + stories + '\n ]}\n')


def display_date(date):
    y, m, d = date.split('-')
    return f'{d} / {m} / {y}', f'{d}/{m}/{y}'

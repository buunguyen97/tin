"""Who owns a story's photo, and whether a public video may use it.

The credit is read from the article itself: the caption or alt text of the <img> showing the share
image ("... Ảnh: VGP"). Share images are often a crop made for social media that the article never
shows; the first photo of the article is then the one they were cut from.

Photos handed out by state bodies and companies (VGP, Bộ Công an, HSG, MSB...) are used; photos
by the paper's own reporters, by news agencies, made with AI, or without a credit are not.
"""
import html, re
from urllib.parse import urlsplit

TAG = re.compile(r'<[^>]+>')
IMG = re.compile(r'<img\b[^>]*>', re.I)
CONTENT_IMG = re.compile(r'<img\b[^>]*(?:itemprop="contentUrl"|rel="lightbox"|data-original=)[^>]*>', re.I)
CAPTION = re.compile(r'<(figcaption|p|div|span|td)\b[^>]*class="[^"]*caption[^"]*"[^>]*>(.*?)</\1>', re.I | re.S)
FIGCAPTION = re.compile(r'<figcaption\b[^>]*>(.*?)</figcaption>', re.I | re.S)
CREDIT = re.compile(r'(?:Ảnh minh họa|Ảnh minh hoạ|Nguồn ảnh|Ảnh|Photo)\s*[:：]\s*([^.()<>"\n]{2,60})', re.I)

# News agencies sell their photos; other papers own theirs.
# An "AI" credit is a picture the paper generated: not handed out, so not used either.
AGENCIES = re.compile(r'\b(reuters|afp|ap|getty|epa|xinhua|bloomberg|ttxvn|vna|shutterstock|tân hoa xã|ai)\b'
                      r'|^(báo|tạp chí)\s|vnexpress|tuổi trẻ|thanh niên|dân trí|vietnamnet|cafef|vneconomy|zing'
                      r'|nhân dân|lao động|vtv|vov|vietnamplus|sggp|an ninh thủ đô|tiền phong|người lao động', re.I)
# State bodies, organisers and companies hand photos out to the press; free libraries license them.
OFFICIAL = re.compile(r'\b(vgp|chính phủ|quốc hội|bộ|sở|ubnd|ủy ban|uỷ ban|công an|cảnh sát|viện kiểm sát|tòa án|toà án'
                      r'|cổng thông tin|bql|ban quản lý|ban tổ chức|btc|tỉnh|thành phố|gov|tập đoàn|tổng công ty|công ty'
                      r'|ngân hàng|airlines|cung cấp|unsplash|pexels|pixabay|wikimedia|tự chụp)\b', re.I)
# Free photo libraries: a stand-in picture of the topic, not of the event, so the card says so.
STOCK = re.compile(r'wikimedia|unsplash|pexels|pixabay|openverse', re.I)
# A word of a person's name: "Ngọc", "L", in capitals with Vietnamese marks ("HỮU"), or a short
# lowercase piece of a name split by the page ("L an Hương"). Tickers and brands are plain ASCII
# capitals ("HSG") or mixed case ("VinEnergo").
# A contributor ("CTV") or a reporter's initials ("SĐ", "N.H"); tickers are plain ASCII ("HSG").
REPORTER_TAG = re.compile(r'^(?i:ctv|pv|nhóm pv)$|^(?=\S*[À-Ỹ])[A-ZÀ-Ỹ](?:\.?[A-ZÀ-Ỹ]){1,2}\.?$|^[A-Z](?:\.[A-Z]){1,2}\.?$')
# A foreign company's Vietnamese arm ("Obayashi Việt Nam"), not a person named "... Việt Nam".
COMPANY_VN = re.compile(r'^[A-Z][A-Za-z&.]+(?: [A-Z][A-Za-z&.]+)* (?:Việt Nam|Vietnam)$')
WORD = re.compile(r'^(?:[A-ZÀ-Ỹ][a-zà-ỹ]*|[A-ZÀ-Ỹ]*[À-Ỹ][A-ZÀ-Ỹ]*|[a-zà-ỹ]{1,3})$')


def _clean(s):
    return re.sub(r'\s+', ' ', html.unescape(TAG.sub(' ', s))).strip()


def _credit_after(page, m):
    tag = m.group(0)
    alt = ' '.join(html.unescape(x) for x in re.findall(r'(?:alt|title)="([^"]*)"', tag))
    after = page[m.end():m.end() + 3000]
    cap = CAPTION.search(after)
    fig = FIGCAPTION.search(after)
    caption = _clean(cap.group(2)) if cap else _clean(fig.group(1)) if fig else _clean(alt)
    found = CREDIT.search(caption) or CREDIT.search(alt)
    return found.group(1).strip(' .;,)') if found else ''


def photo_credit(page, image_url):
    """Credit of `image_url` in the article `page` (HTML); '' when the article doesn't name one."""
    stem = re.sub(r'\.[a-z]+$', '', urlsplit(image_url).path.rsplit('/', 1)[-1])[:25]
    if stem:
        for m in IMG.finditer(page):
            if stem in m.group(0):
                return _credit_after(page, m)
    m = CONTENT_IMG.search(page)
    return _credit_after(page, m) if m else ''


def is_stock(credit):
    """Whether a photo credited to `credit` is a library picture standing in for the story's own."""
    return bool(STOCK.search(credit or ''))


def judge(credit):
    """(usable, reason) for a photo credited to `credit`."""
    credit = (credit or '').strip()
    if not credit:
        return False, 'bài không ghi nguồn ảnh'
    if AGENCIES.search(credit):
        return False, f'ảnh của {credit}'
    if OFFICIAL.search(credit):
        return True, f'ảnh {credit}'
    if REPORTER_TAG.match(credit):
        return False, f'ảnh phóng viên {credit}'
    if COMPANY_VN.search(credit):
        return True, f'ảnh {credit}'
    words = credit.replace('-', ' ').replace(',', ' ').split()  # "HÙNG - TRIỀU": two reporters
    if 2 <= len(words) <= 6 and all(WORD.match(w) for w in words):
        return False, f'ảnh phóng viên {credit}'
    # Tickers and brand names (HSG, MSB, PVN, AEF 2026, VinEnergo): the company's own photo.
    return True, f'ảnh {credit}'

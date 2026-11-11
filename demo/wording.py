"""Wording that can cost a video its reach on TikTok, checked on news stories, captions and reup scripts.

TikTok publishes no list of banned words: these rules come from its policies on sexual services and regulated
goods, and from keeping accounts of deaths sober. A sum of money next to a barred service reads like an advert.
"""
import re

BARRED = re.compile(r'mại dâm|bán dâm|ma túy|ma tuý|súng|vũ khí|đánh bạc|cá độ', re.I)
MONEY = re.compile(r'\btỷ\b|(?:\d|triệu|nghìn|ngàn|trăm|mươi|một|hai|ba|bốn|năm|sáu|bảy|tám|chín|mười)\s*'
                   r'(?:đồng|USD|đô la|đô)\b(?!\s+(?:phạm|bọn|nghiệp|chí|hương|minh|thời|ý))', re.I)
SENTENCES = re.compile(r'(?<=[.!?…;])\s+')
DEATHS = re.compile(r'người chết', re.I)


MASKS = [('mại d', 'âm'), ('bán d', 'âm'), ('ma t', 'úy'), ('ma t', 'uý'), ('s', 'úng'), ('v', 'ũ khí'), ('đánh b', 'ạc'), ('cá đ', 'ộ')]
MASK = re.compile(r'\b(' + '|'.join(f'{a}(?={b}\\b)' for a, b in MASKS) + ')', re.I)
VOICE = {'mại dâm': 'mãi dam', 'bán dâm': 'bán dam', 'ma túy': 'ma tuy', 'ma tuý': 'ma tuy'}
VOICED = re.compile('|'.join(VOICE), re.I)


def mask(text):
    """What is written on screen and in captions: 'mại dâm' -> 'mại d.âm', in the case of the original."""
    return MASK.sub(lambda m: m.group() + '.', text or '')


def to_voice(text):
    """What the voice reads, as the owner chose: 'mại dâm' is read 'mãi dam'. Same number of words as `text`."""
    return VOICED.sub(lambda m: VOICE[m.group().lower()], text or '')


def money_near_barred(text):
    """The sentences of `text` that name a sum of money and a barred service together."""
    return [s for s in SENTENCES.split(text or '') if BARRED.search(s) and MONEY.search(s)]


def soften(text):
    """'17 người chết' -> '17 người thiệt mạng', in the case of the original."""
    def plain(match):
        found = match.group()
        return 'NGƯỜI THIỆT MẠNG' if found.isupper() else 'Người thiệt mạng' if found[0].isupper() else 'người thiệt mạng'
    return DEATHS.sub(plain, text or '')


def problems(text):
    """What is wrong with `text`, in words a writer can act on; empty when it may go out."""
    found = []
    if money_near_barred(text):
        found.append('Bỏ giá và số tiền: câu nói về ma túy, vũ khí, mại dâm hay cờ bạc không được nêu tiền.')
    if re.search(r'\bchết\b', text or '', re.I):
        found.append('Viết "thiệt mạng" thay cho "chết".')
    return found


# --- numbers in captions: the voice needs words, the eye reads digits faster -------------------------

DIGITS = {'không': 0, 'một': 1, 'hai': 2, 'ba': 3, 'bốn': 4, 'năm': 5, 'sáu': 6, 'bảy': 7, 'tám': 8, 'chín': 9}
AFTER_TEN = {**{k: v for k, v in DIGITS.items() if v and k != 'năm'}, 'lăm': 5}  # mười lăm, never "mười năm" (ten years)
AFTER_TENS = {**AFTER_TEN, 'mốt': 1, 'tư': 4}
THOUSAND = {'nghìn', 'ngàn'}
NUMBER_WORDS = set(DIGITS) | set(AFTER_TENS) | THOUSAND | {'mười', 'mươi', 'trăm', 'linh', 'lẻ', 'phẩy', 'chấm', 'phần'}
EDGE = re.compile(r'^(\W*)(.*?)(\W*)$', re.S)


def bare(word):
    return EDGE.match(word)[2].lower()


def is_number_word(word):
    """Could `word` be part of a number read aloud? A caption piece ending in one, before a piece starting with one, may hold half a number."""
    return bare(word) in NUMBER_WORDS


def _below_thousand(words, i):
    """(value, next index, is it more than one bare digit) of the number under 1000 starting at words[i]; None when none starts there,
    False when the words can be read two ways ("một trăm sáu": 106 or 160)."""
    def at(k):
        return words[k] if k < len(words) else ''
    value, start = 0, i
    if at(i) in DIGITS and at(i + 1) == 'trăm':
        value, i = DIGITS[at(i)] * 100, i + 2
        if at(i) in ('linh', 'lẻ') and at(i + 1) in DIGITS:
            return value + DIGITS[at(i + 1)], i + 2, True
    if at(i) == 'mười':
        value, i = value + 10, i + 1
        if at(i) in AFTER_TEN:
            value, i = value + AFTER_TEN[at(i)], i + 1
        return value, i, True
    if at(i) in DIGITS and DIGITS[at(i)] > 1 and at(i + 1) == 'mươi':
        value, i = value + DIGITS[at(i)] * 10, i + 2
        if at(i) in AFTER_TENS:
            value, i = value + AFTER_TENS[at(i)], i + 1
        return value, i, True
    if i > start:  # "ba trăm" and then a bare digit: "ba trăm năm" is 300 years, the others are unclear
        return (value, i, True) if at(i) not in DIGITS or at(i) == 'năm' else False
    if at(i) in DIGITS:
        return DIGITS[at(i)], i + 1, False
    return None


def _whole(words, i):
    """As _below_thousand, for a number that may have thousands."""
    first = _below_thousand(words, i)
    if not first:
        return first
    value, i, strong = first
    if i < len(words) and words[i] in THOUSAND and value:
        rest = _below_thousand(words, i + 1)
        if rest is False or (rest and not rest[2] and words[i + 1] != 'năm'):  # "ba nghìn hai": 3.002 or 3.200
            return False
        if rest and rest[2]:
            return value * 1000 + rest[0], rest[1], True
        return value * 1000, i + 1, True
    return value, i, strong


def _shown(value, year=False):
    return str(value) if year or value < 1000 else f'{value:,}'.replace(',', '.')


def numerals(text):
    """`text` with the numbers it spells out written in digits: 'bốn mươi mốt tuổi' -> '41 tuổi', 'hai mươi tám phẩy bảy mươi lăm
    điểm' -> '28,75 điểm', 'hai phần trăm' -> '2%'. A lone digit word stays ('một quân nhân', 'bốn năm tù'), and so does a number
    that can be read two ways. Words outside numbers are untouched."""
    words = (text or '').split(' ')
    plain = [bare(w) for w in words]
    closed = [bool(EDGE.match(w)[3]) for w in words]  # punctuation after a word ends the number there
    opened = [bool(EDGE.match(w)[1]) for w in words]
    out, i = [], 0
    while i < len(words):
        end = next((k + 1 for k in range(i, len(words)) if closed[k] or (k + 1 < len(words) and opened[k + 1])), len(words))
        found = _whole(plain[:end], i) if plain[i] in NUMBER_WORDS else None
        if found is False:  # unclear: the whole run of number words stays as it is
            while i < end and plain[i] in NUMBER_WORDS:
                out.append(words[i]); i += 1
            continue
        if not found or plain[found[1]:found[1] + 1] in (['mấy'], ['vài']):
            out.append(words[i]); i += 1
            continue
        value, j, strong = found
        shown = _shown(value, year=i > 0 and plain[i - 1] == 'năm' and 1900 <= value <= 2100)
        while j + 1 < end and plain[j] in ('phẩy', 'chấm'):  # 28,75 and 27.2.1
            part = _whole(plain[:end], j + 1)
            if not part:
                break
            shown += (',' if plain[j] == 'phẩy' else '.') + str(part[0]); j, strong = part[1], True
        if plain[j:j + 2] == ['phần', 'trăm'] and j + 2 <= end:
            shown += '%'; j, strong = j + 2, True
        if not strong:
            out.append(words[i]); i += 1
            continue
        out.append(EDGE.match(words[i])[1] + shown + EDGE.match(words[j - 1])[3]); i = j
    return ' '.join(out)

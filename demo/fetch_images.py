"""Download each story's og:image as its video background (internal-use builds only).

  fetch_images.py                         # legacy demo/news.json
  fetch_images.py --workdir W1 W2 ...     # one or more demo/work/<date>/<programme>/ folders

Images are cached in demo/assets/img-<hash>.<ext>, so a story shared by a sector video and the
round-up is downloaded once. Each workdir gets images.json: story number -> cached file.

With "image_rights": "kiem-tra" in config.json (the default), a photo is used only when its credit
allows it (image_rights.py): the sheet's "Ảnh nguồn" if the editor filled it in, else the caption
in the article, cached in demo/assets/credit-<hash>.txt.

A story left without a usable photo (none in the sheet, rights, download failure) gets a CC0 or
public-domain photo of its topic (STOCK_TOPICS: gold, police, court, bank) or else of its sector from
demo/stock/<folder>/ (credits in demo/stock/credits.json), a different one for each such story
of a video. prepare.py tags those "Ảnh minh họa".

A story shows up to MAX_PHOTOS photos in turn (prepare.py, the player's Slideshow): the share image,
then the article's other photos whose own caption allows them (image_rights.article_photos), cached
as demo/assets/img-art-<hash>.<ext>.

When the article gives fewer than two, unless config.json says "ai_image": "tat", Claude looks for
photos (found_photos): it proposes searches ("Từ khóa ảnh" in the sheet is tried first), this file
fetches free photos from Wikimedia Commons (CC0, public domain, and CC BY / CC BY-SA, whose author
and licence the card names), and Claude looks at them and picks up to MAX_PHOTOS that fit, cached as
demo/assets/img-found-<hash>-<n>.jpg and tagged "Ảnh minh họa". It also looks at the first frames of
free Commons clips and may pick one (found_clip), shown among the photos and tagged "Video minh họa".
If nothing fits, Claude draws an illustration (ai_image.py), cached as demo/assets/img-ai-<hash>.png
and tagged "Đồ họa AI". The library photos are what is left when that fails.

A story whose sheet row has a "Video URL" with a free licence ("Video nguồn": Wikimedia Commons
CC0/public domain, NASA; image_rights.video_allowed) also gets the clip, cached as
demo/assets/vid-<hash>.mp4; prepare.py plays it in the card's photo area.
"""
import argparse, hashlib, json, os, re, shutil, subprocess, sys, tempfile, time
from html import unescape
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

from PIL import Image

import ai_image
from edition import load_edition
from image_rights import article_photos, handed_out, is_drawn, judge, photo_credit, video_allowed
from reup import ff

root = Path(__file__).resolve().parent
assets = root / 'assets'
stock = root / 'stock'
STOCK_DEFAULT = 'vi_mo_chinh_sach'  # stories without a sector, or of one without photos
# A topic photo fits better than a sector one: gold bars for a gold price story, not a laptop.
STOCK_TOPICS = [('vang', re.compile(r'\bvàng\b', re.I)),
                ('canh_sat', re.compile(r'trốn trại|trại giam|phạm nhân|truy nã|bắt giữ|tạm giam|khởi tố|công an|cảnh sát', re.I)),
                ('phap_luat', re.compile(r'xét xử|hầu tòa|tòa án|bị cáo|truy tố|bản án|tham ô', re.I)),
                ('tai_chinh_ngan_hang', re.compile(r'ngân hàng|tiết kiệm', re.I))]
DOMESTIC = {'canh_sat'}  # a Vietnamese police car would mislabel an arrest abroad
CONFIG_FILE = Path(os.environ.get('DIEMTIN_CONFIG', root / 'config.json'))
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36'
EXT = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp'}
META = re.compile(r'<meta\s[^>]*>', re.I)
ATTR = re.compile(r'([\w:-]+)\s*=\s*("([^"]*)"|\'([^\']*)\')')


def get(url, accept='*/*'):
    # curl rather than urllib: it uses the system trust store and copes with
    # Vietnamese news CDNs that only answer on IPv4 or plain http.
    marker = b'\n--CONTENT-TYPE:'
    p = subprocess.run(['curl', '-sSL', '--max-time', '30', '-A', UA, '-H', f'Accept: {accept}',
                        '-w', marker.decode() + '%{content_type}', url], capture_output=True)
    if p.returncode:
        raise ValueError(p.stderr.decode().strip())
    body, _, ctype = p.stdout.rpartition(marker)
    return body, ctype.decode().split(';')[0].strip().lower()


def article(page_url):
    for attempt in range(1, 4):
        try:
            return get(page_url, 'text/html')[0].decode('utf-8', 'replace')
        except ValueError:
            if attempt == 3:
                raise
            time.sleep(2 * attempt)


def og_image(page_url, html=None):
    html = html if html is not None else article(page_url)
    found = {}
    for tag in META.findall(html):
        attrs = {k.lower(): unescape(v1 or v2) for k, _, v1, v2 in ATTR.findall(tag)}
        key = (attrs.get('property') or attrs.get('name') or '').lower()
        if key in ('og:image', 'og:image:url', 'og:image:secure_url', 'twitter:image') and attrs.get('content'):
            found.setdefault(key, urljoin(page_url, attrs['content'].strip()))
    for key in ('og:image:secure_url', 'og:image', 'og:image:url', 'twitter:image'):
        if key in found:
            return found[key]
    return None


def image_key(story):
    # news.json may pin an image (e.g. when the article page has no usable og:image).
    return hashlib.sha1((story.get('image') or story['url']).encode()).hexdigest()[:16]


def story_credit(story):
    """Who owns the story's photo: the editor's "Ảnh nguồn", else the article's caption ('' if none)."""
    if story.get('image_credit'):
        return story['image_credit']
    cache = assets / f'credit-{image_key(story)}.txt'
    if cache.exists():
        cache.touch()  # auto_render.py keeps recently used cache files
        return cache.read_text(encoding='utf-8')
    page = article(story['url'])
    credit = photo_credit(page, story.get('image') or og_image(story['url'], page) or '')
    cache.write_text(credit, encoding='utf-8')
    return credit


def fetch_story_image(story):
    """Return the cached file name for a story's image, downloading it if needed."""
    key = image_key(story)
    cached = sorted(assets.glob(f'img-{key}.*'))
    if cached:
        cached[0].touch()  # auto_render.py keeps recently used cache files
        return cached[0].name, True
    for attempt in range(1, 4):  # news sites intermittently serve pages without meta tags
        try:
            image_url = story.get('image') or og_image(story['url'])
            if not image_url:
                raise ValueError('no og:image')
            data, ctype = get(image_url, 'image/*')
            if ctype not in EXT or len(data) < 5000:
                raise ValueError(f'unexpected image ({ctype}, {len(data)} bytes)')
            name = f'img-{key}{EXT[ctype]}'
            (assets / name).write_bytes(data)
            return name, False
        except Exception as e:
            if attempt == 3:
                raise
            print('  thử lại', attempt, e, flush=True)
            time.sleep(2 * attempt)


def fetch_url_photo(url):
    """Cached file name of the photo at `url`, or None when it is not a news photo (too small, an odd shape)."""
    key = hashlib.sha1(url.encode()).hexdigest()[:16]
    cached = sorted(assets.glob(f'img-art-{key}.*'))
    if cached:
        cached[0].touch()  # auto_render.py keeps recently used cache files
        return cached[0].name
    try:
        data, ctype = get(url, 'image/*')
    except ValueError:
        return None
    if ctype not in EXT or len(data) < 20000:
        return None
    path = assets / f'img-art-{key}{EXT[ctype]}'
    path.write_bytes(data)
    try:
        with Image.open(path) as im:
            fits = im.width >= 700 and 0.8 <= im.width / im.height <= 2.4
    except OSError:
        fits = False
    if not fits:
        path.unlink(missing_ok=True)
        return None
    return path.name


def stem_of(url):
    return re.sub(r'\.[a-z]+$', '', urlsplit(url or '').path.rsplit('/', 1)[-1])[:25]


def more_article_photos(story, check_rights, most):
    """[(file, credit)] of the article's other photos whose own caption allows them (all of them without
    the rights check), not counting the share image, at most `most`."""
    page = article(story['url'])
    shared = stem_of(story.get('image') or og_image(story['url'], page))
    found = []
    for url, credit in article_photos(page):
        if len(found) >= most:
            break
        if (shared and stem_of(url) == shared) or (check_rights and not handed_out(credit)):
            continue
        name = fetch_url_photo(url)
        if name:
            found.append((name, credit))
    return found


CLIP_SECONDS = 60  # longest clip kept; a shorter one loops under the voice


def fetch_story_clip(story):
    """(clip, still) cached file names for the story's video: an H.264 copy without sound, at most
    CLIP_SECONDS long and 1920 px wide (a full-width card shows it 1080 px wide and taller: less would be
    blown up and soft), and a frame from it that stands in as the story's photo."""
    key = hashlib.sha1(story['video'].encode()).hexdigest()[:16]
    clip, still = assets / f'vid-{key}.mp4', assets / f'img-vid-{key}.jpg'
    if not (clip.exists() and still.exists()):
        # ffmpeg reads only the first CLIP_SECONDS of the link: library originals can be gigabytes.
        part = assets / f'vid-{key}.part.mp4'
        try:
            ff('-user_agent', UA, '-t', CLIP_SECONDS, '-i', story['video'], '-an', '-vf', "scale='min(1920,iw)':-2,fps=25",
               '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', part)
            part.replace(clip)
        finally:
            part.unlink(missing_ok=True)
        ff('-ss', 1, '-i', clip, '-frames:v', 1, '-q:v', 3, still)
    clip.touch(); still.touch()  # auto_render.py keeps recently used cache files
    return clip.name, still.name


COMMONS_API = 'https://commons.wikimedia.org/w/api.php'
COMMONS_UA = 'diem-tin/1.0 (news video tool)'  # Wikimedia asks scripts for their own name, not a browser's
# CC0 and public domain need nothing; CC BY and CC BY-SA need the author and licence named, which the
# card does (image_rights.photo_label). NonCommercial and NoDerivatives licences are not free enough.
FREE_LICENCE = re.compile(r'^(cc0|public domain|pd-|pd$|cc[ -]by(-sa)?(\s+\d(\.\d)?)?(\s+\w+)?$)', re.I)
ARTIST_TAG = re.compile(r'<[^>]+>')
# Public domain is mostly old scans: a stereo card of 1900 Kiev says nothing about today's Kyiv.
OLD_OR_DRAWN = re.compile(r'\b1[5-9][0-5]\d\b|stereo|postcard|engraving|lithograph|painting|drawing|map\b|logo|coat of arms'
                          r'|\bloc\b|library of congress|brady-handy', re.I)


def commons_search(query, kind='bitmap'):
    """Commons files of `kind` ('bitmap' or 'video') for `query`, as dicts (title, licence, artist, url,
    thumb, duration), best match first, under a free licence (FREE_LICENCE), modern and landscape."""
    url = (f'{COMMONS_API}?action=query&format=json&generator=search&gsrnamespace=6&gsrlimit=30'
           f'&gsrsearch={quote(f"{query} filetype:{kind}")}&prop=imageinfo&iiprop=url|size|extmetadata'
           f'&iiurlwidth={1920 if kind == "bitmap" else 640}&iiextmetadatafilter=LicenseShortName|DateTimeOriginal|Artist')
    p = subprocess.run(['curl', '-sSL', '--max-time', '30', '-A', COMMONS_UA, url], capture_output=True)
    try:
        pages = json.loads(p.stdout).get('query', {}).get('pages', {}) if not p.returncode else {}
    except ValueError:  # Commons answers busy or rate-limited calls with an HTML page
        return []
    found = []
    for page in sorted(pages.values(), key=lambda pg: pg.get('index', 99)):
        title = page.get('title', '')
        info = (page.get('imageinfo') or [{}])[0]
        meta = info.get('extmetadata', {})
        licence = meta.get('LicenseShortName', {}).get('value', '').strip()
        taken = re.search(r'(?:18|19|20)\d\d', meta.get('DateTimeOriginal', {}).get('value', ''))
        width, height = info.get('width', 0), max(info.get('height', 1), 1)
        if (OLD_OR_DRAWN.search(title) or not FREE_LICENCE.match(licence) or (taken and int(taken.group()) < 1990)
                or width < (1200 if kind == 'bitmap' else 1280) or not 1 <= width / height <= 2.2):  # no portraits, no panoramas
            continue
        if kind == 'video' and not 5 <= float(info.get('duration') or 0) <= 1800:
            continue
        artist = re.sub(r'\s+', ' ', unescape(ARTIST_TAG.sub(' ', meta.get('Artist', {}).get('value', '')))).strip()[:40]
        found.append({'title': title.removeprefix('File:'), 'licence': licence, 'artist': artist or 'Wikimedia Commons',
                      'url': info['url'] if kind == 'video' else info.get('thumburl') or info['url'],
                      'thumb': info.get('thumburl', ''), 'duration': info.get('duration')})
    return found


def commons_candidates(query):
    """Photos Commons has for `query` (commons_search)."""
    return commons_search(query, 'bitmap')


def commons_credit(found):
    """The credit kept for a Commons file: its licence, and its author when the licence asks to name one."""
    if re.match(r'cc[ -]by', found['licence'], re.I):
        return f'Wikimedia Commons ({found["licence"]}, {found.get("artist") or "Wikimedia Commons"})'
    return f'Wikimedia Commons ({found["licence"]})'


def download_photo(url, out):
    """Save the photo at `url` as the JPEG `out`; False when it can't be fetched or read."""
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / 'raw'
        p = subprocess.run(['curl', '-sSL', '--max-time', '60', '-A', COMMONS_UA, '-o', str(raw), url], capture_output=True)
        if p.returncode or not raw.exists() or raw.stat().st_size < 20000:
            return False
        try:
            with Image.open(raw) as im:
                im.convert('RGB').save(out, 'JPEG', quality=90)
        except OSError:
            return False
    return True


CANDIDATES_PER_SEARCH, MAX_CANDIDATES = 4, 12
MAX_PHOTOS = 3  # photos a story shows in turn while it is read


def search_list(story, terms):
    """The Commons searches: `terms`, or what a callable `terms` gives (asked only on a cache miss), or Claude's."""
    if callable(terms):
        return terms()
    return terms if terms is not None else ai_image.search_terms(story, story.get('image_query', ''))


def found_photos(story, terms=None, most=MAX_PHOTOS):
    """[(cached file name, credit)] of up to `most` free photos Claude chose for `story` after looking
    at them, best first; [] when Claude saw none that fits. That verdict is cached too, so a re-render
    doesn't search again. `terms`: the Commons searches (ai_image.search_terms when not given)."""
    key = image_key(story)
    note = assets / f'found-{key}.json'
    if note.exists():
        note.touch()
        kept = [(name, credit) for name, credit in json.loads(note.read_text(encoding='utf-8')) if (assets / name).exists()]
        for name, _ in kept:
            (assets / name).touch()
        return kept[:most]
    candidates = {}
    for term in search_list(story, terms):
        for photo in commons_candidates(term)[:CANDIDATES_PER_SEARCH]:
            candidates.setdefault(photo['url'], photo)
        if len(candidates) >= MAX_CANDIDATES:
            break
        time.sleep(1)  # Commons refuses scripts that call it too fast
    if not candidates:
        return []  # possibly a Commons hiccup: not a verdict, so not cached
    with tempfile.TemporaryDirectory() as folder:
        photos, sources = {}, {}
        for photo in list(candidates.values())[:MAX_CANDIDATES]:
            file = f'{len(photos) + 1:02d}.jpg'
            if download_photo(photo['url'], Path(folder) / file):
                photos[file], sources[file] = photo['title'], photo
        if not photos:
            return []
        kept = []
        for k, picked in enumerate(ai_image.pick_photos(story, folder, photos, most), 1):
            name = f'img-found-{key}-{k}.jpg'
            shutil.copyfile(Path(folder) / picked, assets / name)
            kept.append((name, commons_credit(sources[picked])))
    note.write_text(json.dumps(kept, ensure_ascii=False), encoding='utf-8')
    return kept


def found_clip(story, terms=None):
    """(clip url, credit) of a free Commons clip Claude chose for `story` after looking at its first frame,
    or None; the verdict is cached like found_photos'."""
    note = assets / f'found-clip-{image_key(story)}.json'
    if note.exists():
        note.touch()
        return tuple(json.loads(note.read_text(encoding='utf-8')) or ()) or None
    candidates = {}
    for term in search_list(story, terms):
        for clip in commons_search(term, 'video')[:CANDIDATES_PER_SEARCH]:
            if clip['thumb']:
                candidates.setdefault(clip['url'], clip)
        if len(candidates) >= MAX_CANDIDATES:
            break
        time.sleep(1)
    if not candidates:
        return None
    with tempfile.TemporaryDirectory() as folder:
        stills, sources = {}, {}
        for clip in list(candidates.values())[:MAX_CANDIDATES]:
            file = f'{len(stills) + 1:02d}.jpg'
            if download_photo(clip['thumb'], Path(folder) / file):
                stills[file], sources[file] = clip['title'], clip
        if not stills:
            return None
        picked = ai_image.pick_clip(story, folder, stills)
    chosen = (sources[picked]['url'], commons_credit(sources[picked])) if picked else None
    note.write_text(json.dumps(chosen or [], ensure_ascii=False), encoding='utf-8')
    return chosen


def drawn_picture(story):
    """(cached file name, credit) of the illustration Claude drew for `story`, drawing it if needed."""
    name = f'img-ai-{image_key(story)}.png'
    if not (assets / name).exists():
        ai_image.draw(story, assets / name)
    (assets / name).touch()  # auto_render.py keeps recently used cache files
    return name, ai_image.CREDIT


def stock_files(story, used):
    """Library photos for `story`: of its topic while one is still unused, else of its sector."""
    text = ' '.join([*story.get('title', []), story.get('category', '')])
    for folder, pattern in STOCK_TOPICS:
        files = sorted(stock.glob(f'{folder}/*.jpg'))
        if folder in DOMESTIC and story.get('sector') == 'quoc_te':
            continue
        if pattern.search(text) and set(files) - used:
            return files
    return sorted(stock.glob(f'{story.get("sector", "")}/*.jpg')) or sorted(stock.glob(f'{STOCK_DEFAULT}/*.jpg'))


def stock_photo(story, used):
    """(cached file name, credit) of a topic or sector photo for `story` not in `used`, or None
    without any. The pick follows the story URL, so re-rendering a video keeps its photos."""
    credits = json.loads((stock / 'credits.json').read_text(encoding='utf-8')) if (stock / 'credits.json').exists() else {}
    files = stock_files(story, used)
    if not files:
        return None
    start = int(hashlib.sha1(story['url'].encode()).hexdigest(), 16)
    for k in range(len(files)):
        path = files[(start + k) % len(files)]
        if path not in used or k == len(files) - 1:  # more stories than photos: repeat the last pick
            break
    used.add(path)
    name = f'img-stock-{path.parent.name}-{path.stem}.jpg'
    if not (assets / name).exists():
        shutil.copyfile(path, assets / name)
    (assets / name).touch()  # auto_render.py keeps recently used cache files
    info = credits.get(f'{path.parent.name}/{path.name}', {})
    return name, f'Wikimedia Commons ({info.get("license", "CC0")})'


def process(workdir, legacy, check_rights=True, use_claude=True):
    """images.json for the workdir: story number -> its photos ('photos': [{file, credit, video?}], shown in
    turn; 'file' and 'credit' are the first), or a curated clip from the sheet ('video')."""
    edition = load_edition(root / 'news.json' if legacy else workdir / 'news.json')
    manifest, failed, used = {}, 0, set()
    for i, story in enumerate(edition['stories'], 1):
        title = ' '.join(story['title'])
        credit, reason, pics = None, None, []
        if story.get('image') is False:  # the sheet says the article has no relevant photo
            reason = 'bài không có ảnh phù hợp'
        elif check_rights:
            try:
                credit = story_credit(story)
                usable, why = judge(credit)
            except Exception as e:  # unknown owner: not used
                usable, why = False, f'không mở được bài để xem nguồn ảnh ({e})'
            if not usable:
                reason = f'BỎ ẢNH, {why}'
        if not reason:
            try:
                name, cached = fetch_story_image(story)
                pics.append({'file': name, **({'credit': credit} if credit else {})})
                print(f'  {i}. {title}: {"có sẵn" if cached else "đã tải"} {name}{f" (ảnh: {credit})" if credit else ""}', flush=True)
            except Exception as e:
                failed += 1
                reason = f'CẢNH BÁO: không tải được ảnh ({e})'
        # The sheet's "none" is about the share image (a reporter's photo, say): photos further down the
        # article may still be handed out by a state body or a company, and the rights check reads each caption.
        if check_rights or story.get('image') is not False:
            try:
                more = more_article_photos(story, check_rights, MAX_PHOTOS - len(pics))
            except Exception as e:  # the page didn't load: the share image or stand-ins still do
                more = []
                print(f'  {i}. {title}: không đọc được ảnh trong bài ({e})', flush=True)
            for name, by in more:
                pics.append({'file': name, **({'credit': by} if by else {})})
                print(f'  {i}. {title}: thêm ảnh trong bài {name}{f" (ảnh: {by})" if by else ""}', flush=True)
        memo = {}
        def terms(story=story, memo=memo):  # asked of Claude once per story, and only when a search runs
            if 'terms' not in memo:
                memo['terms'] = ai_image.search_terms(story, story.get('image_query', ''))
            return memo['terms']
        if len(pics) < 2 and use_claude:  # the article's own photos are few: free photos of the subject fill in
            try:
                found = found_photos(story, terms, MAX_PHOTOS - len(pics))
            except Exception as e:  # no CLI, out of quota, an odd answer: drawing or the library may still work
                found = []
                print(f'  {i}. {title}: Claude không tìm được ảnh ({e})', flush=True)
            for name, by in found:
                pics.append({'file': name, 'credit': by})
                print(f'  {i}. {title}: {reason + "; " if reason else ""}Claude chọn ảnh {name} ({by})', flush=True)
        if use_claude and not story.get('video'):  # moving pictures first: a free clip, when one fits, opens the story
            try:
                clip = found_clip(story, terms)
                if clip:
                    moving, still = fetch_story_clip({'video': clip[0]})
                    pics.insert(0, {'file': still, 'credit': clip[1], 'video': moving})
                    print(f'  {i}. {title}: Claude chọn video {moving} ({clip[1]})', flush=True)
            except Exception as e:
                print(f'  {i}. {title}: không lấy được video minh họa ({e})', flush=True)
        if not pics and use_claude:
            try:
                name, by = drawn_picture(story)
                pics.append({'file': name, 'credit': by})
                print(f'  {i}. {title}: {reason}; Claude vẽ ảnh {name}', flush=True)
            except Exception as e:  # no CLI, out of quota, a bad drawing: the library still has a photo
                print(f'  {i}. {title}: Claude không vẽ được ({e})', flush=True)
        if not pics:
            picked = stock_photo(story, used)
            if picked:
                pics.append({'file': picked[0], 'credit': picked[1]})
                print(f'  {i}. {title}: {reason}; dùng ảnh minh họa {picked[0]}', flush=True)
            else:  # prepare.py falls back to the plain background
                print(f'  {i}. {title}: {reason}; dùng nền không ảnh', flush=True)
        if pics:
            manifest[str(i)] = {'article': story['url'], 'file': pics[0]['file'], **({'credit': pics[0]['credit']} if pics[0].get('credit') else {}),
                                'photos': pics}
        if story.get('video'):
            usable, why = video_allowed(story.get('video_credit'))
            if not usable:
                print(f'  {i}. {title}: BỎ VIDEO, {why}', flush=True)
                continue
            try:
                clip, still = fetch_story_clip(story)
            except Exception as e:
                failed += 1
                print(f'  {i}. {title}: CẢNH BÁO: không tải được video ({e})', flush=True)
                continue
            entry = manifest.setdefault(str(i), {'article': story['url'], 'file': still, 'credit': story['video_credit']})
            entry.update(video=clip, video_credit=story['video_credit'])
            print(f'  {i}. {title}: video {clip} ({story["video_credit"]})', flush=True)
    (workdir / 'images.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return failed


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--workdir', nargs='+', type=Path, help='Thư mục video (mặc định: demo/)')
    args = parser.parse_args()
    assets.mkdir(exist_ok=True)
    config = json.loads(CONFIG_FILE.read_text(encoding='utf-8')) if CONFIG_FILE.exists() else {}
    check_rights = config.get('image_rights', 'kiem-tra') != 'tat'
    failed = 0
    for wd in args.workdir or [root]:
        print(f'[{wd.name}]', flush=True)
        failed += process(wd.resolve(), legacy=args.workdir is None, check_rights=check_rights,
                          use_claude=config.get('ai_image', 'bat') != 'tat')
    print('ảnh lỗi:', failed)


if __name__ == '__main__':
    main()

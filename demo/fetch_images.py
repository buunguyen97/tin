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

Before the library, a story whose row has "Từ khóa ảnh" (English words: "Knesset building") gets
the first CC0 or public-domain photo Wikimedia Commons finds for them (commons_photo), cached as
demo/assets/img-commons-<hash>.jpg. It is a stand-in too, so it is tagged "Ảnh minh họa".

A story whose sheet row has a "Video URL" with a free licence ("Video nguồn": Wikimedia Commons
CC0/public domain, NASA; image_rights.video_allowed) also gets the clip, cached as
demo/assets/vid-<hash>.mp4; prepare.py plays it in the card's photo area.
"""
import argparse, hashlib, json, os, re, shutil, subprocess, sys, time
from html import unescape
from pathlib import Path
from urllib.parse import quote, urljoin

from edition import load_edition
from image_rights import judge, photo_credit, video_allowed
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


CLIP_SECONDS = 60  # longest clip kept; a shorter one loops under the voice


def fetch_story_clip(story):
    """(clip, still) cached file names for the story's video: an H.264 copy without sound, at most
    CLIP_SECONDS long and 1280 px wide, and a frame from it that stands in as the story's photo."""
    key = hashlib.sha1(story['video'].encode()).hexdigest()[:16]
    clip, still = assets / f'vid-{key}.mp4', assets / f'img-vid-{key}.jpg'
    if not (clip.exists() and still.exists()):
        # ffmpeg reads only the first CLIP_SECONDS of the link: library originals can be gigabytes.
        part = assets / f'vid-{key}.part.mp4'
        try:
            ff('-user_agent', UA, '-t', CLIP_SECONDS, '-i', story['video'], '-an', '-vf', "scale='min(1280,iw)':-2,fps=25",
               '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '22', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', part)
            part.replace(clip)
        finally:
            part.unlink(missing_ok=True)
        ff('-ss', 1, '-i', clip, '-frames:v', 1, '-q:v', 3, still)
    clip.touch(); still.touch()  # auto_render.py keeps recently used cache files
    return clip.name, still.name


COMMONS_API = 'https://commons.wikimedia.org/w/api.php'
COMMONS_UA = 'diem-tin/1.0 (news video tool)'  # Wikimedia asks scripts for their own name, not a browser's
FREE_LICENCE = re.compile(r'^(cc0|public domain|pd-|pd$)', re.I)
# Public domain is mostly old scans: a stereo card of 1900 Kiev says nothing about today's Kyiv.
OLD_OR_DRAWN = re.compile(r'\b1[5-9][0-5]\d\b|stereo|postcard|engraving|lithograph|painting|drawing|map\b|logo|coat of arms'
                          r'|\bloc\b|library of congress|brady-handy', re.I)


def commons_photo(query):
    """(cached file name, credit) of the first CC0 / public-domain landscape photo Commons finds for
    `query` whose file name holds every word of it ("Knesset building") and doesn't look like an
    old scan or a drawing, or None. The answer is cached per query, so a re-render keeps the photo."""
    key = hashlib.sha1(query.lower().encode()).hexdigest()[:16]
    name, note = f'img-commons-{key}.jpg', assets / f'credit-commons-{key}.txt'
    if (assets / name).exists() and note.exists():
        (assets / name).touch(); note.touch()
        return name, note.read_text(encoding='utf-8')
    url = (f'{COMMONS_API}?action=query&format=json&generator=search&gsrnamespace=6&gsrlimit=40'
           f'&gsrsearch={quote(query + " filetype:bitmap haswbstatement:P275=Q6938433|P6216=Q19652")}&prop=imageinfo&iiprop=url|size|extmetadata'
           f'&iiurlwidth=1920&iiextmetadatafilter=LicenseShortName|DateTimeOriginal')
    p = subprocess.run(['curl', '-sSL', '--max-time', '30', '-A', COMMONS_UA, url], capture_output=True)
    try:
        pages = json.loads(p.stdout).get('query', {}).get('pages', {}) if not p.returncode else {}
    except ValueError:  # Commons answers busy or rate-limited calls with an HTML page
        return None
    words = [w for w in re.findall(r'\w+', query.lower()) if len(w) > 2]
    for page in sorted(pages.values(), key=lambda pg: pg.get('index', 99)):
        title = page.get('title', '')
        if not all(w in title.lower() for w in words) or OLD_OR_DRAWN.search(title):  # search also matches descriptions
            continue
        info = (page.get('imageinfo') or [{}])[0]
        meta = info.get('extmetadata', {})
        licence = meta.get('LicenseShortName', {}).get('value', '')
        taken = re.search(r'(?:18|19|20)\d\d', meta.get('DateTimeOriginal', {}).get('value', ''))
        if taken and int(taken.group()) < 1990:
            continue
        if not FREE_LICENCE.match(licence) or info.get('width', 0) < 1200 or not 1 <= info.get('width', 0) / max(info.get('height', 1), 1) <= 2.2:  # no portraits, no panoramas
            continue
        p = subprocess.run(['curl', '-sSL', '--max-time', '60', '-A', COMMONS_UA, '-o', str(assets / name),
                            info.get('thumburl') or info['url']], capture_output=True)
        if p.returncode or not (assets / name).exists() or (assets / name).stat().st_size < 20000:
            (assets / name).unlink(missing_ok=True)
            continue
        credit = f'Wikimedia Commons ({licence})'
        note.write_text(credit, encoding='utf-8')
        return name, credit
    return None


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


def process(workdir, legacy, check_rights=True):
    edition = load_edition(root / 'news.json' if legacy else workdir / 'news.json')
    manifest, failed, used = {}, 0, set()
    for i, story in enumerate(edition['stories'], 1):
        title = ' '.join(story['title'])
        credit, reason = None, None
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
                manifest[str(i)] = {'article': story['url'], 'file': name, **({'credit': credit} if credit else {})}
                print(f'  {i}. {title}: {"có sẵn" if cached else "đã tải"} {name}{f" (ảnh: {credit})" if credit else ""}', flush=True)
            except Exception as e:
                failed += 1
                reason = f'CẢNH BÁO: không tải được ảnh ({e})'
        if reason and story.get('image_query'):
            try:
                found = commons_photo(story['image_query'])
            except Exception as e:
                found = None
                print(f'  {i}. {title}: không tìm được ảnh Wikimedia ({e})', flush=True)
            if found:
                manifest[str(i)] = {'article': story['url'], 'file': found[0], 'credit': found[1]}
                print(f'  {i}. {title}: {reason}; dùng ảnh Wikimedia "{story["image_query"]}" {found[0]}', flush=True)
                reason = None
        if reason:
            picked = stock_photo(story, used)
            if picked:
                manifest[str(i)] = {'article': story['url'], 'file': picked[0], 'credit': picked[1]}
                print(f'  {i}. {title}: {reason}; dùng ảnh minh họa {picked[0]}', flush=True)
            else:  # prepare.py falls back to the plain background
                print(f'  {i}. {title}: {reason}; dùng nền không ảnh', flush=True)
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
        failed += process(wd.resolve(), legacy=args.workdir is None, check_rights=check_rights)
    print('ảnh lỗi:', failed)


if __name__ == '__main__':
    main()

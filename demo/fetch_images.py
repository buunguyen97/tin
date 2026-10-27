"""Download each story's og:image as its video background (internal-use builds only).

  fetch_images.py                         # legacy demo/news.json
  fetch_images.py --workdir W1 W2 ...     # one or more demo/work/<date>/<programme>/ folders

Images are cached in demo/assets/img-<hash>.<ext>, so a story shared by a sector video and the
round-up is downloaded once. Each workdir gets images.json: story number -> cached file.
"""
import argparse, hashlib, json, re, subprocess, sys, time
from html import unescape
from pathlib import Path
from urllib.parse import urljoin

from edition import load_edition

root = Path(__file__).resolve().parent
assets = root / 'assets'
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


def og_image(page_url):
    html, _ = get(page_url, 'text/html')
    html = html.decode('utf-8', 'replace')
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


def fetch_story_image(story):
    """Return the cached file name for a story's image, downloading it if needed."""
    # news.json may pin an image (e.g. when the article page has no usable og:image).
    key = hashlib.sha1((story.get('image') or story['url']).encode()).hexdigest()[:16]
    cached = sorted(assets.glob(f'img-{key}.*'))
    if cached:
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


def process(workdir, legacy):
    edition = load_edition(root / 'news.json' if legacy else workdir / 'news.json')
    manifest, failed = {}, 0
    for i, story in enumerate(edition['stories'], 1):
        title = ' '.join(story['title'])
        if story.get('image') is False:  # article has no relevant photo; prepare.py draws a plain background
            print(f'  {i}. {title}: không dùng ảnh', flush=True)
            continue
        try:
            name, cached = fetch_story_image(story)
            manifest[str(i)] = {'article': story['url'], 'file': name}
            print(f'  {i}. {title}: {"có sẵn" if cached else "đã tải"} {name}', flush=True)
        except Exception as e:  # keep going: prepare.py falls back to the plain background
            failed += 1
            print(f'CẢNH BÁO: tin "{title}" không tải được ảnh ({e}); dùng nền gradient', flush=True)
    (workdir / 'images.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return failed


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--workdir', nargs='+', type=Path, help='Thư mục video (mặc định: demo/)')
    args = parser.parse_args()
    assets.mkdir(exist_ok=True)
    failed = 0
    for wd in args.workdir or [root]:
        print(f'[{wd.name}]', flush=True)
        failed += process(wd.resolve(), legacy=args.workdir is None)
    print('ảnh lỗi:', failed)


if __name__ == '__main__':
    main()

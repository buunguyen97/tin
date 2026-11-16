"""Bring a finished video's sound to the loudness of other short videos; the picture isn't re-encoded.

  .venv-demo/bin/python demo/loudness.py output/daily/2026-11-07/*.mp4
"""
import json, re, sys
from pathlib import Path

from reup import ff, ff_log

TARGET = 'I=-14:TP=-1.5:LRA=11'  # LUFS, dBTP, LU


def measure(video):
    """What loudnorm's first pass found: its JSON report, or None if the file has no measurable sound."""
    found = re.search(r'\{[^{}]*"input_i"[^{}]*\}', ff_log('-i', video, '-vn', '-af', f'loudnorm={TARGET}:print_format=json'))
    if not found:
        return None
    report = json.loads(found.group(0))
    return report if re.fullmatch(r'-?\d+(\.\d+)?', report['input_i']) else None  # silence reads "-inf"


def normalize(video):
    """Rewrite `video` in place at TARGET. True if it did, False if there was nothing to measure."""
    video = Path(video)
    m = measure(video)
    if not m:
        return False
    settings = (f"{TARGET}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
                f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    temporary = video.with_name(video.stem + '.loud' + video.suffix)  # a failed run leaves the video untouched
    try:
        ff('-i', video, '-map', '0:v', '-map', '0:a', '-c:v', 'copy', '-af', f'loudnorm={settings}',
           '-ar', 48000, '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', temporary)
        temporary.replace(video)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def try_normalize(video):
    """For the end of a render: a video that keeps its quieter sound is still worth having."""
    try:
        if not normalize(video):
            print(f'CẢNH BÁO: không đo được âm lượng của {Path(video).name}', flush=True)
    except Exception as e:
        print(f'CẢNH BÁO: chưa chỉnh được âm lượng của {Path(video).name}: {e}', flush=True)


if __name__ == '__main__':
    for path in sys.argv[1:]:
        try_normalize(path)

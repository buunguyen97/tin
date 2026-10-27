"""Provider-independent audio timing and content-addressed cache paths."""
import hashlib
import json
import math
import wave
from pathlib import Path


def audio_duration(path):
    path = Path(path)
    if path.suffix.lower() == '.wav':
        with wave.open(str(path), 'rb') as audio:
            duration = audio.getnframes() / audio.getframerate()
    else:
        from mutagen.mp3 import MP3
        from mutagen import MutagenError
        try:
            duration = MP3(path).info.length
        except MutagenError as exc:
            raise ValueError(f"Invalid MP3: {path}") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(f'Audio has no usable duration: {path}')
    return duration


def cache_path(directory, *, provider, voice, model, text):
    payload = json.dumps([provider, voice, model, text], ensure_ascii=False).encode('utf-8')
    digest = hashlib.sha256(payload).hexdigest()[:24]
    extension = '.wav' if provider == 'vieneu' else '.mp3'
    return Path(directory) / f'{provider}-{digest}{extension}'

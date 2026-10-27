import tempfile
import unittest
import wave
from pathlib import Path
from tts_audio import audio_duration, cache_path

class AudioTests(unittest.TestCase):
    def test_wav_duration_uses_sample_count(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'voice.wav'
            with wave.open(str(p), 'wb') as f:
                f.setnchannels(1); f.setsampwidth(2); f.setframerate(48000)
                f.writeframes(b'\0\0' * 60000)
            self.assertEqual(audio_duration(p), 1.25)

    def test_cache_does_not_reuse_audio_after_input_changes(self):
        settings = dict(provider='vieneu', voice='Mai Anh', model='v3turbo', text='Tin hôm nay.')
        original = cache_path(Path('/tmp'), **settings)
        self.assertEqual(original, cache_path(Path('/tmp'), **settings))
        self.assertEqual(original.suffix, '.wav')
        for key, value in [('voice', 'Hải Đăng'), ('text', 'Tin mới.'), ('model', 'v3nano'), ('provider', 'edge')]:
            self.assertNotEqual(original, cache_path(Path('/tmp'), **{**settings, key: value}))

    def test_corrupt_mp3_has_a_recoverable_validation_error(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'corrupt.mp3'
            p.write_bytes(b'interrupted download')
            with self.assertRaises(ValueError): audio_duration(p)

    def test_empty_wav_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'empty.wav'
            with wave.open(str(p),'wb') as f:
                f.setnchannels(1); f.setsampwidth(2); f.setframerate(48000)
            with self.assertRaises(ValueError): audio_duration(p)

if __name__ == '__main__': unittest.main()

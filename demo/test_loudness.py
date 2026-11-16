"""Run from demo/: ../.venv-demo/Scripts/python.exe -m unittest test_loudness"""
import subprocess, tempfile, unittest
from pathlib import Path
from unittest import mock

import loudness
from reup import ff, ffmpeg


def make(path, sound):
    ff('-f', 'lavfi', '-i', 'color=c=black:s=64x64:r=25:d=8', '-f', 'lavfi', '-i', sound, '-t', 8,
       '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', path)


def video_bytes(path, folder):
    out = Path(folder) / f'{Path(path).stem}.h264'
    ff('-i', path, '-map', '0:v', '-c', 'copy', '-f', 'h264', out)
    return out.read_bytes()


class Loudness(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name)

    def test_quiet_video_reaches_the_target_and_keeps_its_picture(self):
        video = self.dir / 'a.mp4'
        make(video, 'sine=frequency=440:duration=8,volume=0.05')
        before = video_bytes(video, self.dir)
        self.assertTrue(loudness.normalize(video))
        self.assertAlmostEqual(float(loudness.measure(video)['input_i']), -14, delta=1)
        self.assertEqual(before, video_bytes(video, self.dir))

    def test_silent_video_is_left_alone(self):
        video = self.dir / 'a.mp4'
        make(video, 'anullsrc=r=48000:cl=stereo')
        before = video.read_bytes()
        self.assertFalse(loudness.normalize(video))
        self.assertEqual(before, video.read_bytes())

    def test_failure_keeps_the_video_and_leaves_no_temporary_file(self):
        video = self.dir / 'a.mp4'
        make(video, 'sine=frequency=440:duration=8')
        before = video.read_bytes()
        with mock.patch('loudness.ff', side_effect=subprocess.CalledProcessError(1, ffmpeg())):
            loudness.try_normalize(video)  # must not raise: the render still counts
        self.assertEqual(before, video.read_bytes())
        self.assertEqual([p.name for p in self.dir.iterdir()], ['a.mp4'])


if __name__ == '__main__':
    unittest.main()

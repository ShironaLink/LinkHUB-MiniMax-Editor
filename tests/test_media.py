import importlib.util
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

import av
import numpy as np


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "genso" / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


media = load_module("genso_media", "media.py")
qc = load_module("genso_qc", "qc.py")


def make_video(path: Path, black=False, with_audio=False):
    with av.open(str(path), mode="w", format="mp4") as container:
        stream = container.add_stream("libx264", rate=30)
        stream.width = 320
        stream.height = 240
        stream.pix_fmt = "yuv420p"
        audio = None
        if with_audio:
            audio = container.add_stream("aac", rate=48000)
            audio.layout = "stereo"
        for index in range(60):
            if black:
                pixels = np.zeros((240, 320, 3), dtype=np.uint8)
            else:
                pixels = np.zeros((240, 320, 3), dtype=np.uint8)
                pixels[..., 0] = (index * 3) % 255
                pixels[..., 1] = np.arange(320, dtype=np.uint8)[None, :]
                pixels[..., 2] = np.arange(240, dtype=np.uint8)[:, None]
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            frame.pts = index
            frame.time_base = Fraction(1, 30)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
        if audio is not None:
            for index in range(20):
                samples = 4800
                timeline = (np.arange(samples) + index * samples) / 48000
                wave = (0.08 * np.sin(2 * np.pi * 440 * timeline)).astype(np.float32)
                frame = av.AudioFrame.from_ndarray(np.stack([wave, wave]), format="fltp", layout="stereo")
                frame.sample_rate = 48000
                frame.pts = index * samples
                frame.time_base = Fraction(1, 48000)
                for packet in audio.encode(frame):
                    container.mux(packet)
            for packet in audio.encode():
                container.mux(packet)


class MediaTests(unittest.TestCase):
    def test_probe_preprocess_and_qc(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source = directory / "source.mp4"
            make_video(source)
            before = media.probe(source)
            self.assertEqual(before["kind"], "video")
            self.assertAlmostEqual(before["fps"], 30, places=1)

            result = media.preprocess_reference_video(source, directory, source.name, 160, 120)
            self.assertEqual(result["fps"], 24)
            self.assertEqual((result["width"], result["height"]), (160, 120))
            self.assertIn("24fps", result["note"])
            verdict = qc.inspect_video(directory / result["name"])
            self.assertEqual(verdict["verdict"], "OK")

    def test_black_video_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "black.mp4"
            make_video(target, black=True)
            self.assertEqual(qc.inspect_video(target)["verdict"], "BLACK")

    def test_preprocess_preserves_optional_audio_track(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source = directory / "with_audio.mp4"
            make_video(source, with_audio=True)
            self.assertTrue(media.probe(source)["has_audio"])
            result = media.preprocess_reference_video(source, directory, source.name, 160, 120)
            self.assertTrue(result["has_audio"])
            audio = qc.inspect_video(directory / result["name"])["stats"]["audio"]
            self.assertTrue(audio["present"])
            self.assertGreater(audio["peak"], 0.001)


if __name__ == "__main__":
    unittest.main()

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("genso_graph", ROOT / "genso" / "graph.py")
graph = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(graph)


def base_settings(**updates):
    settings = {
        "mode": "t2va",
        "prompt": "a test scene. Audio: quiet room tone only.",
        "width": 864,
        "height": 480,
        "length": 124,
        "steps": 8,
        "seed": 123,
        "sampler": "res_multistep",
        "scheduler": "simple",
        "shift_video": 12.0,
        "shift_audio": 6.0,
        "loras": [],
        "gguf": False,
        "text_encoder": graph.TEXT_ENCODER_GGUF,
        "first_frame": None,
        "last_frame": None,
        "ref_images": [],
        "ref_videos": [],
        "ref_video_use_audio": [],
        "ref_audios": [],
        "ref_image_size": "match",
        "out_name": "test",
    }
    settings.update(updates)
    return settings


class GraphTests(unittest.TestCase):
    def test_length_alignment(self):
        self.assertEqual(graph.align_length(29), 39)
        self.assertEqual(graph.align_length(124), 124)
        self.assertEqual(graph.align_length(200), 209)
        self.assertEqual(graph.align_length(999), 362)

    def test_t2va_uses_genso_conditioning_for_gguf_headroom(self):
        workflow = graph.build_graph(base_settings())
        self.assertEqual(workflow["1"]["class_type"], "UNETLoader")
        self.assertEqual(workflow["1"]["inputs"]["weight_dtype"], "default")
        self.assertEqual(workflow["3"]["inputs"]["model"], ["1", 0])
        self.assertEqual(workflow["4"]["inputs"]["type"], "minimax")
        self.assertEqual(workflow["4"]["class_type"], "CLIPLoaderGGUF")
        self.assertEqual(workflow["7"]["class_type"], "GensoH3ImageToVideo")

    def test_nvfp4_awq_uses_core_clip_loader(self):
        workflow = graph.build_graph(base_settings(text_encoder=graph.TEXT_ENCODER_NVFP4))
        self.assertEqual(workflow["4"]["class_type"], "CLIPLoader")
        self.assertEqual(workflow["4"]["inputs"], {
            "clip_name": graph.TEXT_ENCODER_NVFP4,
            "type": "minimax",
            "device": "default",
        })

    def test_multiple_loras_are_chained_at_fixed_ids(self):
        workflow = graph.build_graph(base_settings(loras=[
            {"name": "a.safetensors", "strength": 0.5},
            {"name": "b.safetensors", "strength": 1.0},
            {"name": "c.safetensors", "strength": 1.2},
        ]))
        self.assertEqual(workflow["2"]["inputs"]["model"], ["1", 0])
        self.assertEqual(workflow["30"]["inputs"]["model"], ["2", 0])
        self.assertEqual(workflow["31"]["inputs"]["model"], ["30", 0])
        self.assertEqual(workflow["3"]["inputs"]["model"], ["31", 0])

    def test_fl2va_keyframes(self):
        workflow = graph.build_graph(base_settings(
            mode="fl2va", first_frame="first.png", last_frame="last.png"
        ))
        self.assertEqual(workflow["7"]["inputs"]["first_frame"], ["21", 0])
        self.assertEqual(workflow["7"]["inputs"]["last_frame"], ["22", 0])
        self.assertEqual(workflow["7"]["class_type"], "GensoH3ImageToVideo")

    def test_ref2va_all_autogrow_inputs(self):
        workflow = graph.build_graph(base_settings(
            mode="ref2va",
            ref_images=[f"image{i}.png" for i in range(9)],
            ref_videos=[f"video{i}.mp4" for i in range(3)],
            ref_video_use_audio=[True, False, True],
            ref_audios=[f"audio{i}.mp3" for i in range(3)],
        ))
        inputs = workflow["7"]["inputs"]
        self.assertEqual(workflow["7"]["class_type"], "GensoH3ReferenceToVideo")

        # Autogrow slots must be submitted as flat dot-paths. A nested dict is
        # accepted by /prompt without complaint and then silently discarded, so
        # the run completes having ignored every reference. Asserting the exact
        # key shape is what keeps that failure from coming back unnoticed.
        def slots(prefix: str) -> dict[str, object]:
            return {k: v for k, v in inputs.items() if k.startswith(f"{prefix}.")}

        self.assertNotIn("ref_images", inputs)
        images = slots("ref_images")
        self.assertEqual(len(images), 9)
        self.assertEqual(images["ref_images.ref_image_8"], ["60", 0])
        self.assertEqual(images["ref_images.ref_image_9"], ["61", 0])
        self.assertEqual(len(slots("ref_videos")), 3)
        self.assertEqual(
            set(slots("ref_video_audios")),
            {"ref_video_audios.ref_video_audio_1", "ref_video_audios.ref_video_audio_3"},
        )
        self.assertEqual(len(slots("ref_audios")), 3)


if __name__ == "__main__":
    unittest.main()

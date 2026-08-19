import fnmatch
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkflowAdoptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.known_loras = json.loads(
            (ROOT / "genso" / "known_loras.json").read_text(encoding="utf-8")
        )

    def known_for(self, filename):
        for pattern, metadata in self.known_loras.items():
            if fnmatch.fnmatch(filename.casefold(), pattern.casefold()):
                return metadata
        self.fail(f"LoRA is not registered: {filename}")

    def test_lightx2v_fl2v_workflow_settings(self):
        metadata = self.known_for(
            "minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_"
            "comfy_resized_avg_rank_21_bf16.safetensors"
        )
        self.assertEqual(metadata["modes"], ["t2va", "fl2va"])
        self.assertEqual(metadata["steps"], 4)
        self.assertEqual(metadata["sampler"], "er_sde")
        self.assertEqual(metadata["default_strength"], 0.75)

    def test_lightx2v_ref2v_workflow_settings(self):
        metadata = self.known_for(
            "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors"
        )
        self.assertEqual(metadata["modes"], ["ref2va"])
        self.assertEqual(metadata["steps"], 4)
        self.assertEqual(metadata["sampler"], "euler")
        self.assertEqual(metadata["default_strength"], 1.0)

    def test_hmnsfw_aio_v2_is_registered_for_h3_modes(self):
        metadata = self.known_for("HMNSFW_AIO_V2.safetensors")
        self.assertEqual(metadata["kind"], "content")
        self.assertEqual(metadata["modes"], ["t2va", "fl2va", "ref2va"])
        self.assertEqual(metadata["default_strength"], 1.0)

    def test_reference_declaration_is_attribute_transfer(self):
        templates = json.loads(
            (ROOT / "genso" / "templates.json").read_text(encoding="utf-8")
        )
        declaration = next(item for item in templates if item["id"] == "ref2va_declaration")
        self.assertIn("Retention: <Subject 1> is attribute_transfer.", declaration["body"])

    def test_er_sde_is_selectable(self):
        html = (ROOT / "genso" / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("<option>er_sde</option>", html)

    def test_reference_picture_is_not_a_first_frame_by_default(self):
        script = (ROOT / "genso" / "web" / "js" / "app.js").read_text(encoding="utf-8")
        self.assertIn(
            'if (key === "Use <Picture 1> as the first frame, exactly as it is.") return false;',
            script,
        )

    def test_text_encoder_selector_is_wired_to_generation_payload(self):
        html = (ROOT / "genso" / "web" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "genso" / "web" / "js" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="textEncoder"', html)
        self.assertIn('text_encoder: $("#textEncoder").value', script)

    def test_restart_rewrites_stale_external_shell_mode(self):
        script = (ROOT / "genso" / "web" / "js" / "app.js").read_text(encoding="utf-8")
        self.assertIn(
            'nextUrl.searchParams.set("shell", result.external === true ? "external" : "owned")',
            script,
        )
        self.assertIn("location.replace(nextUrl.toString())", script)

    def test_frontend_can_reload_without_restarting_engine(self):
        html = (ROOT / "genso" / "web" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "genso" / "web" / "js" / "app.js").read_text(encoding="utf-8")
        wrapper = (ROOT / "wrapper" / "app.py").read_text(encoding="utf-8")
        self.assertIn('id="refreshButton"', html)
        self.assertIn("function refreshFrontend()", script)
        self.assertIn('nextUrl.searchParams.set("ui", Date.now().toString())', script)
        self.assertIn('os.environ.get("GENSO_ATTACH_OWNED") == "1"', wrapper)
        self.assertIn("self.attached_owned_pid = self._foreign_comfy_pid()", wrapper)

    def test_display_name_is_consistent(self):
        display_name = "LinkHUB　零式　MINI MAX簡易エディタ"
        html = (ROOT / "genso" / "web" / "index.html").read_text(encoding="utf-8")
        wrapper = (ROOT / "wrapper" / "app.py").read_text(encoding="utf-8")
        self.assertIn(f"<title>{display_name}</title>", html)
        self.assertIn(f'WINDOW_TITLE = "{display_name}"', wrapper)

    def test_window_height_matches_obs_16_by_9_capture(self):
        wrapper = (ROOT / "wrapper" / "app.py").read_text(encoding="utf-8")
        self.assertIn("width=1280,\n        height=750,", wrapper)

    def test_prompt_drafts_are_isolated_by_mode(self):
        script = (ROOT / "genso" / "web" / "js" / "app.js").read_text(encoding="utf-8")
        self.assertIn("promptDrafts: {", script)
        self.assertIn("state.promptDrafts[previousMode] = prompt.value", script)
        self.assertIn('prompt.value = state.promptDrafts[mode] || ""', script)
        self.assertIn('if (state.mode !== "mannequin") return;', script)

    def test_unreliable_runtime_predictions_are_not_shown(self):
        html = (ROOT / "genso" / "web" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "genso" / "web" / "js" / "app.js").read_text(encoding="utf-8")
        self.assertIn('<option value="draft" selected>下書き（高速）</option>', html)
        self.assertIn("所要時間はメモリ退避状況で大きく変わるため予測しません", html)
        self.assertNotIn("残り約", script)
        self.assertNotIn("steps * 72.6", script)

    def test_first_frame_aspect_ratio_is_preserved_automatically(self):
        html = (ROOT / "genso" / "web" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "genso" / "web" / "js" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "genso" / "web" / "css" / "style.css").read_text(encoding="utf-8")
        self.assertIn('value="source" id="sourceAspectOption"', html)
        self.assertIn("function dimensionsForSourceAspect", script)
        self.assertIn('$("#aspectRatio").value = "source"', script)
        self.assertIn('if (which === "first") {\n      matchFirstImage(false);', script)
        self.assertRegex(css, r"\.file-preview img\s*\{[^}]*object-fit:\s*contain")
        self.assertRegex(css, r"\.file-chip img\s*\{[^}]*object-fit:\s*contain")
        self.assertRegex(css, r"\.history-item video\s*\{[^}]*object-fit:\s*contain")
        self.assertIn("style.css?v=", html)
        self.assertIn("app.js?v=", html)


if __name__ == "__main__":
    unittest.main()

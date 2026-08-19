import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "genso_prompt_compiler", ROOT / "genso" / "prompt_compiler.py"
)
compiler = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = compiler
SPEC.loader.exec_module(compiler)


class PromptCompilerTests(unittest.TestCase):
    def test_llm_receives_only_translatable_leaves(self):
        source = (
            "シーン: <Picture 1> のしろなが笑う。\n"
            "[0.0s - 2.0s] Shot 1: 手を振る。\n"
            "Camera: 固定カメラ。\n"
            "Audio: <d>[Japanese] ミニマックス、すげー！</d> 「そのまま」"
        )
        plan = compiler.build_translation_plan(source)
        llm_input = json.dumps(
            [{"id": item.id, "text": item.text} for item in plan.fragments],
            ensure_ascii=False,
        )

        self.assertNotIn("<Picture 1>", llm_input)
        self.assertNotIn("<d>", llm_input)
        self.assertNotIn("ミニマックス、すげー！", llm_input)
        self.assertNotIn("そのまま", llm_input)
        self.assertIn("Shirona", llm_input)

    def test_reassembly_preserves_dialogue_tags_and_structure(self):
        source = (
            "シーン: <Picture 1> の人物が立つ。\n"
            "[0.0s - 2.0s] Shot 1: 人物が笑う。\n"
            "Camera: 正面で固定。\n"
            "Audio: <d>[Japanese] ミニマックス、すげー！</d>"
        )
        plan = compiler.build_translation_plan(source)
        english = [
            "Scene",
            "The character stands.",
            "The character smiles.",
            "Locked front-facing camera.",
        ]
        translations = {
            fragment.id: value for fragment, value in zip(plan.fragments, english, strict=True)
        }
        output = compiler.render_translation(plan, translations)

        self.assertEqual(
            output,
            "Scene: <Picture 1> The character stands.\n"
            "[0.0s - 2.0s] Shot 1: The character smiles.\n"
            "Camera: Locked front-facing camera.\n"
            "Audio: <d>[Japanese] ミニマックス、すげー！</d>",
        )

    def test_japanese_subtitle_literal_is_byte_exact(self):
        source = '字幕: "ミニマックス、すげー！"\n説明: 下中央に表示する。'
        plan = compiler.build_translation_plan(source)
        self.assertNotIn("ミニマックス", "".join(item.text for item in plan.fragments))
        translations = {
            fragment.id: value
            for fragment, value in zip(
                plan.fragments,
                ("Subtitle", "Description", "Display it at the bottom center."),
                strict=True,
            )
        }
        output = compiler.render_translation(plan, translations)
        self.assertIn('"ミニマックス、すげー！"', output)

    def test_shirona_only_is_deterministic(self):
        plan = compiler.build_translation_plan("しろな")
        self.assertEqual(plan.fragments, ())
        self.assertEqual(compiler.render_translation(plan, {}), "Shirona")

    def test_japanese_sentence_boundaries_get_english_spacing(self):
        plan = compiler.build_translation_plan("固定する。ズームしない。")
        translations = {
            fragment.id: value
            for fragment, value in zip(
                plan.fragments,
                ("Keep it fixed.", "Do not zoom."),
                strict=True,
            )
        }
        self.assertEqual(
            compiler.render_translation(plan, translations),
            "Keep it fixed. Do not zoom.",
        )

    def test_missing_or_structural_translation_fails_closed(self):
        plan = compiler.build_translation_plan("シーン: 少女が笑う。")
        with self.assertRaises(compiler.PromptCompileError):
            compiler.render_translation(plan, {})
        translations = {item.id: "Scene: changed" for item in plan.fragments}
        with self.assertRaises(compiler.PromptCompileError):
            compiler.render_translation(plan, translations)

    def test_translation_json_contract(self):
        content = '{"translations":[{"id":"T0000","text":"A girl smiles."}]}'
        self.assertEqual(
            compiler.parse_translation_response(content),
            {"T0000": "A girl smiles."},
        )
        with self.assertRaises(compiler.PromptCompileError):
            compiler.parse_translation_response('{"T0000":"A girl smiles."}')


if __name__ == "__main__":
    unittest.main()

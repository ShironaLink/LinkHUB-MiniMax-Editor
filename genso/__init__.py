"""GENSO custom node: same-origin UI/API routes and prefixed H3 nodes."""

from __future__ import annotations

import asyncio
import fnmatch
import json
import math
import os
import random
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web
import folder_paths
from server import PromptServer

from .graph import (
    AUDIO_VAE,
    REF2VA_GGUF,
    REF2VA_INT8,
    T2VA_GGUF,
    T2VA_INT8,
    TEXT_ENCODER,
    TEXT_ENCODER_GGUF,
    TEXT_ENCODER_NVFP4,
    TEXT_ENCODER_OPTIONS,
    VIDEO_VAE,
    align_length,
    build_graph,
)
from .media import preprocess_reference_video, probe
from .nodes_override import (
    GensoH3AddKeyframe,
    GensoH3ImageToVideo,
    GensoH3ReferenceToVideo,
)
from .qc import inspect_video
from .prompt_compiler import (
    PromptCompileError,
    build_translation_plan,
    parse_translation_response,
    render_translation,
)
from .resource_guard import generation_vram_error
# Aliased: _comfy_request already uses a local named `session` for the HTTP client.
from . import session as ui_session

NODE_CLASS_MAPPINGS: dict[str, Any] = {
    "GensoH3ImageToVideo": GensoH3ImageToVideo,
    "GensoH3ReferenceToVideo": GensoH3ReferenceToVideo,
    "GensoH3AddKeyframe": GensoH3AddKeyframe,
}
NODE_DISPLAY_NAME_MAPPINGS: dict[str, str] = {
    "GensoH3ImageToVideo": "GENSO H3 Video Conditioning (8GB)",
    "GensoH3ReferenceToVideo": "GENSO H3 Reference to Video (8GB)",
    "GensoH3AddKeyframe": "GENSO H3 Add Keyframe",
}

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
WEB_DIR = HERE / "web"
CONFIG_PATH = ROOT / "config.json"
TEMPLATES_PATH = HERE / "templates.json"
KNOWN_LORAS_PATH = HERE / "known_loras.json"
CLIENT_ID = "genso"
GIB = 1024 ** 3

with CONFIG_PATH.open("r", encoding="utf-8") as handle:
    CONFIG = json.load(handle)
with KNOWN_LORAS_PATH.open("r", encoding="utf-8") as handle:
    KNOWN_LORAS = json.load(handle)

PORT = int(CONFIG["port"])
OUTPUT_SUBFOLDER = str(CONFIG.get("output_subfolder", "GENSO"))
TRANSLATOR_CONFIG = CONFIG.get("translator", {})
OLLAMA_URL = str(TRANSLATOR_CONFIG.get("url", "http://127.0.0.1:11434")).rstrip("/")
OLLAMA_MODEL = str(
    TRANSLATOR_CONFIG.get("model", "huihui_ai/qwen3-abliterated:latest")
)
OLLAMA_TIMEOUT = float(TRANSLATOR_CONFIG.get("timeout_sec", 300))
OLLAMA_KEEP_ALIVE = TRANSLATOR_CONFIG.get("keep_alive", 0)
INPUT_DIR = Path(folder_paths.get_input_directory()).resolve()
OUTPUT_DIR_DISPLAY = Path(folder_paths.get_output_directory()) / OUTPUT_SUBFOLDER
OUTPUT_DIR = OUTPUT_DIR_DISPLAY.resolve()

TRANSLATION_SYSTEM = """Translate each independent Japanese text leaf into concise,
natural English for a MiniMax-H3 video prompt. Existing English and the proper name
Shirona must be preserved. Keep states, actions, restrictions, and chronology faithful;
do not censor, soften, moralize, merge, reorder, omit, or invent content.

Return JSON only in exactly this shape:
{"translations":[{"id":"T0000","text":"English translation"}]}
Return every input id exactly once and in the same order. Each text value must be one
line and must not contain angle brackets, colons, full-width colons, or arrows. The
inputs are isolated leaves; control tags, dialogue, subtitles, and prompt structure are
kept outside the model and will be serialized by the caller."""

MODEL_SPECS = (
    (T2VA_INT8, "dit_fl2va", "diffusion_models", True, "Comfy-Org/MiniMax-H3"),
    (REF2VA_INT8, "dit_ref2va", "diffusion_models", True, "Comfy-Org/MiniMax-H3"),
    (VIDEO_VAE, "vae_video", "vae", True, "Comfy-Org/MiniMax-H3"),
    (AUDIO_VAE, "vae_audio", "vae", True, "Comfy-Org/MiniMax-H3"),
    (TEXT_ENCODER_GGUF, "text_encoder_gguf", "text_encoders", False, "Abiray/MiniMax-H3-GGUF"),
    (TEXT_ENCODER_NVFP4, "text_encoder_nvfp4", "text_encoders", False, "Comfy-Org/MiniMax-H3"),
    (
        "minimax_h3_turbo_4step_ckpt600_ema_V4.safetensors",
        "lora_turbo",
        "loras",
        False,
        "Abiray/MiniMax-H3-Turbo-Lora-Pruned-ComfyUI",
    ),
    (
        "minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy_resized_avg_rank_21_bf16.safetensors",
        "lora_turbo_fl2v_lightx2v",
        "loras",
        False,
        "lightx2v/Minimax-h3-Turbo",
    ),
    (
        "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors",
        "lora_turbo_ref2v_lightx2v",
        "loras",
        False,
        "lightx2v/Minimax-h3-Turbo",
    ),
    (
        "minimax_h3_looping_sketch_anime_v1.safetensors",
        "lora_looping",
        "loras",
        False,
        "Inner-Reflections/MiniMax-H3-Looping-Sketch-Anime",
    ),
    (T2VA_GGUF, "dit_fl2va_gguf", "diffusion_models", False, "Abiray/MiniMax-H3-Pruned-GGUF"),
    (REF2VA_GGUF, "dit_ref2va_gguf", "diffusion_models", False, "Abiray/MiniMax-H3-Pruned-GGUF"),
)


def _json(data: dict[str, Any] | list[Any], status: int = 200) -> web.Response:
    return web.json_response(
        data,
        status=status,
        dumps=lambda value: json.dumps(value, ensure_ascii=False),
    )


def _error(message: str, status: int = 400) -> web.Response:
    return _json({"ok": False, "error": message}, status=status)


def _listed(category: str) -> list[str]:
    # ComfyUI-GGUF exposes GGUF text encoders through its clip_gguf alias on
    # installations whose Stability Matrix config still calls the path "clip".
    if category == "text_encoders":
        categories = ("text_encoders", "clip", "clip_gguf")
    elif category == "diffusion_models":
        categories = ("diffusion_models", "unet", "unet_gguf")
    else:
        categories = (category,)
    found: list[str] = []
    try:
        for current in categories:
            found.extend(folder_paths.get_filename_list(current))
    except Exception:
        pass
    return list(dict.fromkeys(found))


def _contains_file(files: list[str], expected: str) -> bool:
    expected_lower = expected.casefold()
    return any(Path(item).name.casefold() == expected_lower for item in files)


def _matching_file(files: list[str], expected: str) -> str | None:
    expected_lower = expected.casefold()
    return next(
        (item for item in files if Path(item).name.casefold() == expected_lower),
        None,
    )


def _known_lora(name: str) -> dict[str, Any] | None:
    base = Path(name).name.casefold()
    for pattern, info in KNOWN_LORAS.items():
        if fnmatch.fnmatch(base, pattern.casefold()):
            result = dict(info)
            result["pattern"] = pattern
            return result
    return None


def model_inventory() -> dict[str, Any]:
    categories = {category: _listed(category) for *_, category, _, _ in MODEL_SPECS}
    required: list[dict[str, Any]] = []
    optional: list[dict[str, Any]] = []
    for filename, role, category, is_required, source in MODEL_SPECS:
        matched_name = _matching_file(categories[category], filename)
        entry = {
            "file": filename,
            "name": matched_name or filename,
            "role": role,
            "present": matched_name is not None,
            "folder": category,
            "source": source,
        }
        (required if is_required else optional).append(entry)

    text_encoders = [
        entry for entry in optional if entry["role"].startswith("text_encoder_")
    ]
    required.insert(2, {
        "file": f"{TEXT_ENCODER_GGUF} または {TEXT_ENCODER_NVFP4}",
        "name": "text_encoder_alternative",
        "role": "text_encoder_any",
        "present": any(entry["present"] for entry in text_encoders),
        "folder": "text_encoders",
        "source": "Abiray/MiniMax-H3-GGUF / Comfy-Org/MiniMax-H3",
    })

    loras = []
    for name in sorted(_listed("loras"), key=str.casefold):
        known = _known_lora(name)
        if known is not None:
            loras.append({"name": name, "known": known})
    loras.sort(key=lambda item: item["name"].casefold())
    return {
        "required": required,
        "optional": optional,
        "loras": loras,
        "text_encoders": text_encoders,
    }


async def _comfy_request(method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, Any]:
    url = f"http://127.0.0.1:{PORT}{path}"
    session = PromptServer.instance.client_session
    owns_session = session is None
    if owns_session:
        session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
    try:
        async with session.request(method, url, json=payload) as response:
            text = await response.text()
            try:
                body = json.loads(text)
            except json.JSONDecodeError:
                body = {"error": text[:800]}
            return response.status, body
    finally:
        if owns_session:
            await session.close()


def _request_source(request: web.Request) -> str:
    """Who submitted this — a person at the window, or an agent over HTTP.

    Only used for the label on screen; both paths run identical code.
    """
    value = str(request.headers.get("X-GENSO-Source", "")).strip().lower()
    return value if value in {"ui", "api"} else "api"


def _safe_relative_path(root: Path, name: str) -> Path:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("ファイル名が空です")
    relative = Path(name.replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("不正なファイル名です")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("不正なファイル名です") from exc
    return candidate


def _input_file(name: str) -> Path:
    candidate = _safe_relative_path(INPUT_DIR, name)
    if not candidate.is_file():
        raise ValueError(f"入力ファイルが見つかりません: {name}")
    return candidate


def _clean_out_name(value: Any) -> str:
    name = str(value or "genso").strip()
    name = re.sub(r"[^\w.-]+", "_", name, flags=re.UNICODE).strip("._")
    return (name[:100] or "genso")


def _number(value: Any, label: str, cast: type, minimum: float, maximum: float) -> Any:
    try:
        result = cast(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} の値が不正です") from exc
    if not minimum <= result <= maximum:
        raise ValueError(f"{label} は {minimum:g}〜{maximum:g} の範囲にしてください")
    return result


async def _parse_generation(request: web.Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise ValueError("JSON リクエストを読み取れません") from exc
    if not isinstance(payload, dict):
        raise ValueError("リクエスト形式が不正です")

    mode = payload.get("mode", "t2va")
    if mode not in {"t2va", "fl2va", "ref2va"}:
        raise ValueError("mode は t2va / fl2va / ref2va のいずれかです")
    prompt_text = str(payload.get("prompt", "")).strip()
    if not prompt_text:
        raise ValueError("プロンプトを入力してください")

    width = _number(payload.get("width", 864), "幅", int, 32, 4096)
    height = _number(payload.get("height", 480), "高さ", int, 32, 4096)
    if width % 32 or height % 32:
        raise ValueError("幅と高さは32の倍数にしてください")
    length = align_length(_number(payload.get("length", 124), "フレーム数", int, 29, 362))
    steps = _number(payload.get("steps", 8), "steps", int, 1, 100)
    seed_value = payload.get("seed")
    if seed_value is None or str(seed_value).strip() == "":
        seed = random.randrange(2**48)
    else:
        seed = _number(seed_value, "seed", int, 0, 2**48 - 1)

    first_frame = payload.get("first_frame") or None
    last_frame = payload.get("last_frame") or None
    ref_images = list(payload.get("ref_images") or [])
    ref_videos = list(payload.get("ref_videos") or [])
    ref_audios = list(payload.get("ref_audios") or [])
    ref_video_use_audio = [bool(item) for item in (payload.get("ref_video_use_audio") or [])]
    if len(ref_images) > 9:
        raise ValueError("参照画像は最大9枚です")
    if len(ref_videos) > 3:
        raise ValueError("参照動画は最大3本です")
    if len(ref_audios) > 3:
        raise ValueError("参照音声は最大3本です")
    if mode == "ref2va" and not (ref_images or ref_videos or ref_audios):
        raise ValueError("参照して作るモードでは画像・動画・音声を1つ以上追加してください")
    if mode == "fl2va" and not (first_frame or last_frame):
        raise ValueError("画像を動かすモードでは開始または終了フレームが必要です")
    if mode == "ref2va" and last_frame:
        # Only the frame-0 anchor is wired for reference mode; a tail keyframe
        # would need its own layout slot and has never been tested there.
        raise ValueError("参照して作るモードで使えるのは開始フレームだけです")

    for name in [first_frame, last_frame, *ref_images, *ref_videos, *ref_audios]:
        if name:
            _input_file(str(name))

    # The longest standalone audio determines the necessary output duration.
    if ref_audios and bool(payload.get("align_audio", True)):
        durations = [float(probe(_input_file(name)).get("duration_sec", 0)) for name in ref_audios]
        if max(durations, default=0) > 0:
            length = align_length(math.ceil(max(durations) * 24))

    loras = []
    available_loras = _listed("loras")
    for item in payload.get("loras") or []:
        if not isinstance(item, dict) or not item.get("name"):
            raise ValueError("LoRA の指定が不正です")
        name = str(item["name"])
        if not _contains_file(available_loras, Path(name).name):
            raise ValueError(f"LoRA が見つかりません: {name}")
        known = _known_lora(name)
        if known is None:
            raise ValueError(f"MiniMax-H3 対応として登録されていない LoRA です: {name}")
        if mode not in known.get("modes", []):
            raise ValueError(f"現在のモードでは使用できない LoRA です: {name}")
        strength = _number(
            item.get("strength", known.get("default_strength", 1.0)),
            "LoRA強度", float, 0.0, 1.5,
        )
        loras.append({"name": name, "strength": strength})

    ref_image_size = payload.get("ref_image_size", "match")
    if ref_image_size not in {"match", "max"}:
        raise ValueError("ref_image_size は match または max です")
    sampler = str(payload.get("sampler", "res_multistep"))
    if sampler not in {"res_multistep", "euler", "er_sde"}:
        raise ValueError("sampler は res_multistep / euler / er_sde のいずれかです")
    text_encoder = str(payload.get("text_encoder") or TEXT_ENCODER)
    allowed_text_encoders = {name.casefold() for name in TEXT_ENCODER_OPTIONS}
    if Path(text_encoder).name.casefold() not in allowed_text_encoders:
        raise ValueError("未対応のMiniMax-H3テキストエンコーダーです")
    scheduler = str(payload.get("scheduler", "simple"))

    return {
        "mode": mode,
        "prompt": prompt_text,
        "width": width,
        "height": height,
        "length": length,
        "seconds": round(length / 24, 3),
        "steps": steps,
        "seed": seed,
        "sampler": sampler,
        "scheduler": scheduler,
        "shift_video": _number(payload.get("shift_video", 12.0), "video shift", float, 0.01, 100.0),
        "shift_audio": _number(payload.get("shift_audio", 6.0), "audio shift", float, 0.01, 100.0),
        "loras": loras,
        "gguf": bool(payload.get("gguf", False)),
        "text_encoder": text_encoder,
        "first_frame": first_frame,
        "last_frame": last_frame,
        "ref_images": ref_images,
        "ref_videos": ref_videos,
        "ref_video_use_audio": (ref_video_use_audio + [False] * len(ref_videos))[: len(ref_videos)],
        "ref_audios": ref_audios,
        "align_audio": bool(payload.get("align_audio", True)),
        "ref_image_size": ref_image_size,
        "out_name": _clean_out_name(payload.get("out_name")),
    }


def _missing_for(settings: dict[str, Any]) -> list[str]:
    inventory = model_inventory()
    by_name = {
        entry["file"].casefold(): entry["present"]
        for entry in inventory["required"] + inventory["optional"]
    }
    if settings["gguf"]:
        dit = REF2VA_GGUF if settings["mode"] == "ref2va" else T2VA_GGUF
    else:
        dit = REF2VA_INT8 if settings["mode"] == "ref2va" else T2VA_INT8
    needed = [dit, VIDEO_VAE, AUDIO_VAE]
    missing = [name for name in needed if not by_name.get(name.casefold(), False)]
    text_encoder = str(settings.get("text_encoder") or TEXT_ENCODER)
    if not _contains_file(_listed("text_encoders"), Path(text_encoder).name):
        missing.append(text_encoder)
    return missing


routes = PromptServer.instance.routes


@routes.get("/genso")
async def genso_index(_request: web.Request) -> web.StreamResponse:
    return web.FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@routes.get(r"/genso/web/{path:.*}")
async def genso_static(request: web.Request) -> web.StreamResponse:
    try:
        target = _safe_relative_path(WEB_DIR, request.match_info["path"])
    except ValueError:
        return _error("不正なパスです", 403)
    if not target.is_file():
        return _error("ファイルが見つかりません", 404)
    return web.FileResponse(target, headers={"Cache-Control": "no-cache"})


@routes.get("/genso/api/models")
async def genso_models(_request: web.Request) -> web.Response:
    return _json(model_inventory())


@routes.get("/genso/api/status")
async def genso_status(_request: web.Request) -> web.Response:
    status, stats = await _comfy_request("GET", "/system_stats")
    _queue_status, queue = await _comfy_request("GET", "/queue")
    if status != 200:
        return _error("ComfyUI の状態を取得できません", 503)
    system = stats.get("system", {})
    devices = stats.get("devices", [])
    vram_free = float(devices[0].get("vram_free", 0)) / GIB if devices else None
    # Total VRAM decides which limits apply: the frame and resolution ceilings
    # were measured on an 8 GiB card and do not describe a larger one.
    vram_total = float(devices[0].get("vram_total", 0)) / GIB if devices else None
    ram_free = float(system.get("ram_free", 0)) / GIB
    ram_total = float(system.get("ram_total", 0)) / GIB
    return _json(
        {
            "ok": True,
            "models": model_inventory(),
            "vram_free_gb": round(vram_free, 2) if vram_free is not None else None,
            "vram_total_gb": round(vram_total, 2) if vram_total is not None else None,
            "ram_note": f"RAM 空き {ram_free:.1f} / {ram_total:.1f} GiB",
            "queue_running": bool(queue.get("queue_running") or queue.get("queue_pending")),
            "output_dir": str(OUTPUT_DIR_DISPLAY),
            "comfyui_version": system.get("comfyui_version"),
        }
    )


@routes.post("/genso/api/translate")
async def genso_translate(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("リクエスト形式が不正です")
        source = str(payload.get("text", ""))
        if not source.strip():
            raise ValueError("翻訳するプロンプトを入力してください")
        if len(source) > 12000:
            raise ValueError("プロンプトは12000文字以内にしてください")
        plan = build_translation_plan(source)
        if not plan.fragments:
            translated = render_translation(plan, {})
            return _json({
                "ok": True,
                "prompt": translated,
                "model": OLLAMA_MODEL,
                "translation_fragments": 0,
            })

        queue_status, queue = await _comfy_request("GET", "/queue")
        if queue_status != 200:
            return _error("生成キューを確認できません", 503)
        if queue.get("queue_running") or queue.get("queue_pending"):
            return _error("動画生成中は翻訳できません。完了後にもう一度実行してください", 409)

        fragment_payload = {
            "fragments": [
                {"id": fragment.id, "text": fragment.text}
                for fragment in plan.fragments
            ]
        }
        fragment_json = json.dumps(fragment_payload, ensure_ascii=False)
        ollama_payload = {
            "model": OLLAMA_MODEL,
            "messages": [
                {"role": "system", "content": TRANSLATION_SYSTEM},
                {"role": "user", "content": fragment_json},
            ],
            "format": "json",
            "stream": False,
            "think": False,
            "keep_alive": OLLAMA_KEEP_ALIVE,
            "options": {
                "temperature": 0,
                "num_predict": min(8192, max(1200, len(fragment_json))),
            },
        }
        timeout = aiohttp.ClientTimeout(total=OLLAMA_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(f"{OLLAMA_URL}/api/chat", json=ollama_payload) as response:
                body_text = await response.text()
                try:
                    body = json.loads(body_text)
                except json.JSONDecodeError:
                    body = {"error": body_text[:800]}
                if response.status >= 400:
                    detail = body.get("error", body_text[:800])
                    return _error(f"Ollama翻訳に失敗しました: {detail}", 502)

        content = str(body.get("message", {}).get("content", "")).strip()
        if not content:
            return _error("Ollamaから翻訳結果が返りませんでした", 502)
        translations = parse_translation_response(content)
        translated = render_translation(plan, translations)
        return _json({
            "ok": True,
            "prompt": translated,
            "model": OLLAMA_MODEL,
            "translation_fragments": len(plan.fragments),
        })
    except (asyncio.TimeoutError, aiohttp.ServerTimeoutError):
        return _error("Ollama翻訳がタイムアウトしました", 504)
    except aiohttp.ClientError as exc:
        return _error(f"Ollamaへ接続できません: {exc}", 503)
    except PromptCompileError as exc:
        return _error(f"翻訳結果を安全に採用できません: {exc}", 502)
    except ValueError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(f"翻訳中にエラーが発生しました: {exc}", 500)


@routes.post("/genso/api/generate")
async def genso_generate(request: web.Request) -> web.Response:
    try:
        settings = await _parse_generation(request)
        missing = _missing_for(settings)
        if missing:
            return _error("必要なモデルが未配置です: " + ", ".join(missing))

        stats_status, stats = await _comfy_request("GET", "/system_stats")
        if stats_status != 200 or not stats.get("system", {}).get("comfyui_version"):
            return _error("ComfyUI エンジンが応答していません", 503)
        queue_status, queue = await _comfy_request("GET", "/queue")
        if queue_status != 200:
            return _error("生成キューを確認できません", 503)
        if queue.get("queue_running") or queue.get("queue_pending"):
            return _error("別の生成ジョブが実行中です。完了または中断を待ってください", 409)
        devices = stats.get("devices", [])
        if devices:
            vram_free = float(devices[0].get("vram_free", 0)) / GIB
            vram_error = generation_vram_error(vram_free)
            if vram_error:
                return _error(vram_error, 409)

        graph = build_graph(settings)
        submit_status, result = await _comfy_request(
            "POST", "/prompt", {"prompt": graph, "client_id": CLIENT_ID}
        )
        if submit_status >= 400 or "prompt_id" not in result:
            detail = result.get("error", result)
            return _error(f"ComfyUI がグラフを拒否しました: {str(detail)[:800]}", 400)

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        settings_path = OUTPUT_DIR / f"{settings['out_name']}.json"
        with settings_path.open("w", encoding="utf-8") as handle:
            json.dump(settings, handle, ensure_ascii=False, indent=2)
        # Publish the job so every window shows it, not just the one that asked.
        ui_session.start_job(result["prompt_id"], settings, _request_source(request))
        return _json(
            {
                "ok": True,
                "prompt_id": result["prompt_id"],
                "settings": settings,
            }
        )
    except ValueError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(f"生成の準備中にエラーが発生しました: {exc}", 500)


@routes.get("/genso/api/session")
async def genso_session(_request: web.Request) -> web.Response:
    """The shared screen state: what is running, and what is in the form.

    A window calls this on load and after a reconnect, so it can pick up a job
    that someone else — a person at another window, or an agent — started.
    """
    await ui_session.reconcile(_comfy_request)
    return _json({"ok": True, **ui_session.snapshot()})


@routes.post("/genso/api/form")
async def genso_set_form(request: web.Request) -> web.Response:
    """Fill the editor's fields from outside the browser.

    This is the half that lets an agent prepare a generation without taking the
    decision away from the person: the values land in the real form, visibly, and
    the button is still theirs to press.
    """
    try:
        payload = await request.json()
    except Exception:
        return _error("JSON リクエストを読み取れません")
    if not isinstance(payload, dict):
        return _error("リクエスト形式が不正です")
    values = payload.get("values")
    if not isinstance(values, dict):
        return _error("values オブジェクトが必要です")
    try:
        for name in [
            values.get("first_frame"),
            values.get("last_frame"),
            *(values.get("ref_images") or []),
            *(values.get("ref_videos") or []),
            *(values.get("ref_audios") or []),
        ]:
            if name:
                # Fail here rather than letting the window show a file it cannot load.
                _input_file(str(name))
    except ValueError as exc:
        # Callers are often agents, so the reason must come back as JSON, not a 500.
        return _error(str(exc))
    return _json({"ok": True, "form": ui_session.set_form(values, _request_source(request))})


@routes.post("/genso/api/job_finished")
async def genso_job_finished(request: web.Request) -> web.Response:
    """A window reports that the job it was watching ended.

    Advisory only — `/genso/api/session` re-checks with the engine, so a closed
    window cannot leave the screen stuck on a job that is already over.
    """
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    ui_session.finish_job(
        str(payload.get("prompt_id")) if payload.get("prompt_id") else None,
        str(payload.get("status", "done")),
    )
    return _json({"ok": True})


@routes.post("/genso/api/interrupt")
async def genso_interrupt(_request: web.Request) -> web.Response:
    """Stop the running job, and report honestly whether it actually stopped.

    ComfyUI answers /interrupt with 200 even when the sampler cannot act on it:
    the flag is only read between sampling steps, so a job still staging its
    weights or inside its first step keeps going. Saying "stopped" there would
    be a lie, so the queue is polled until the prompt really leaves it. Queued
    (not yet started) jobs are cleared too — pressing stop means stop.
    """
    status, queue = await _comfy_request("GET", "/queue")
    if status != 200:
        return _error("エンジンが応答していません。停止している可能性があります", 503)

    running = queue.get("queue_running") or []
    pending = queue.get("queue_pending") or []
    if not running and not pending:
        ui_session.finish_job(None, "done")
        return _json({"ok": True, "stopped": True, "message": "実行中の生成はありません"})

    if pending:
        await _comfy_request("POST", "/queue", {"clear": True})
    if running:
        await _comfy_request("POST", "/interrupt")

    for _ in range(12):
        await asyncio.sleep(1)
        status, queue = await _comfy_request("GET", "/queue")
        if status != 200:
            break
        if not (queue.get("queue_running") or queue.get("queue_pending")):
            ui_session.finish_job(None, "interrupted")
            return _json({"ok": True, "stopped": True, "message": "生成を中断しました"})

    return _json({
        "ok": True,
        "stopped": False,
        "message": "中断を送りましたが、まだ止まっていません。"
                   "サンプリングの区切りでのみ効くため、"
                   "最初のステップの途中だと待つか、エンジンの再起動が必要です",
    })


@routes.post("/genso/api/preprocess_video")
async def genso_preprocess_video(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
        name = str(payload.get("name", ""))
        source = _input_file(name)
        width = _number(payload.get("target_width"), "目標幅", int, 32, 4096)
        height = _number(payload.get("target_height"), "目標高さ", int, 32, 4096)
        result = await asyncio.to_thread(
            preprocess_reference_video, source, INPUT_DIR, name, width, height
        )
        return _json(result)
    except ValueError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(f"参照動画の前処理に失敗しました: {exc}", 500)


@routes.get("/genso/api/probe")
async def genso_probe(request: web.Request) -> web.Response:
    try:
        path = _input_file(request.query.get("name", ""))
        return _json({"ok": True, **(await asyncio.to_thread(probe, path))})
    except ValueError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(f"メディア情報を取得できません: {exc}", 500)


@routes.post("/genso/api/qc")
async def genso_qc(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
        filename = str(payload.get("filename", ""))
        target = _safe_relative_path(OUTPUT_DIR, filename)
        if not target.is_file() or target.suffix.casefold() != ".mp4":
            raise ValueError("生成動画が見つかりません")
        # The saved settings name the reference audio, so QC can check the clip
        # actually followed the voice it was given instead of only that it is
        # not black.
        settings = _settings_for_video(target) or {}
        reference_audio = None
        for name in settings.get("ref_audios") or []:
            try:
                reference_audio = _input_file(str(name))
                break
            except ValueError:
                continue
        result = await asyncio.to_thread(inspect_video, target, reference_audio)
        return _json({"ok": True, **result})
    except ValueError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(f"QC に失敗しました: {exc}", 500)


def _settings_for_video(video: Path) -> dict[str, Any] | None:
    stem = video.stem
    candidates = [video.with_suffix(".json")]
    base = re.sub(r"_\d{5}_$", "", stem)
    candidates.append(video.parent / f"{base}.json")
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            with candidate.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            continue
    return None


@routes.get("/genso/api/history")
async def genso_history(_request: web.Request) -> web.Response:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    entries = []
    for video in OUTPUT_DIR.glob("*.mp4"):
        try:
            stat = video.stat()
        except OSError:
            continue
        entries.append(
            {
                "filename": video.name,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "settings": _settings_for_video(video),
            }
        )
    entries.sort(key=lambda item: item["mtime"], reverse=True)
    return _json(entries)


@routes.get("/genso/video/{filename}")
async def genso_video(request: web.Request) -> web.StreamResponse:
    try:
        target = _safe_relative_path(OUTPUT_DIR, request.match_info["filename"])
    except ValueError:
        return _error("不正なファイル名です", 403)
    if not target.is_file() or target.suffix.casefold() != ".mp4":
        return _error("動画が見つかりません", 404)
    return web.FileResponse(target)


@routes.post("/genso/api/open_folder")
async def genso_open_folder(_request: web.Request) -> web.Response:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform == "win32":
            os.startfile(str(OUTPUT_DIR_DISPLAY))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(OUTPUT_DIR_DISPLAY)])
        else:
            subprocess.Popen(["xdg-open", str(OUTPUT_DIR_DISPLAY)])
        return _json({"ok": True})
    except Exception as exc:
        return _error(f"出力フォルダを開けません: {exc}", 500)


@routes.get("/genso/api/templates")
async def genso_templates(_request: web.Request) -> web.Response:
    try:
        with TEMPLATES_PATH.open("r", encoding="utf-8") as handle:
            return _json(json.load(handle))
    except Exception as exc:
        return _error(f"テンプレートを読めません: {exc}", 500)

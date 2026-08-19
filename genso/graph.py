"""Canonical MiniMax-H3 API graph builder for GENSO."""

from __future__ import annotations

from typing import Any

T2VA_INT8 = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
REF2VA_INT8 = "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
T2VA_GGUF = "MiniMax-H3-FL2VA-Pruned-Q3_K_M.gguf"
REF2VA_GGUF = "MiniMax-H3-Ref2VA-Pruned-Q3_K_M.gguf"
TEXT_ENCODER_GGUF = "qwen3vl_32b_minimax_h3-Q4_K_M.gguf"
TEXT_ENCODER_NVFP4 = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
# Backward-compatible default used by existing API clients and saved settings.
TEXT_ENCODER = TEXT_ENCODER_GGUF
TEXT_ENCODER_OPTIONS = (TEXT_ENCODER_GGUF, TEXT_ENCODER_NVFP4)
VIDEO_VAE = "minimax_h3_video_vae_fp16.safetensors"
AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"

# The first seven IDs are fixed by the design. 60 and 61 complete the promised
# nine-image capacity without colliding with the LoRA chain at 30+.
REF_IMAGE_NODE_IDS = ("20", "23", "24", "25", "26", "27", "28", "60", "61")
REF_VIDEO_NODE_IDS = (("40", "41"), ("42", "43"), ("44", "45"))
REF_AUDIO_NODE_IDS = ("50", "51", "52")


def align_length(n: int) -> int:
    """Clamp to the trained range, then align upward to MiniMax-H3's 17k+5 grid."""
    n = max(29, min(int(n), 362))
    while n % 17 != 5:
        n += 1
    return n


def build_graph(settings: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Build the fixed-node-ID graph described in sections 4.2 through 4.5."""
    mode = settings["mode"]
    is_ref = mode == "ref2va"
    use_gguf = bool(settings.get("gguf"))
    if use_gguf:
        dit = REF2VA_GGUF if is_ref else T2VA_GGUF
        loader = {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": dit}}
    else:
        dit = REF2VA_INT8 if is_ref else T2VA_INT8
        loader = {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": dit, "weight_dtype": "default"},
        }

    text_encoder = str(settings.get("text_encoder") or TEXT_ENCODER)
    if text_encoder.casefold().endswith(".gguf"):
        clip_loader = {
            "class_type": "CLIPLoaderGGUF",
            "inputs": {"clip_name": text_encoder, "type": "minimax"},
        }
    else:
        clip_loader = {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": text_encoder,
                "type": "minimax",
                "device": "default",
            },
        }

    graph: dict[str, dict[str, Any]] = {
        "1": loader,
        "4": clip_loader,
        "5": {"class_type": "VAELoader", "inputs": {"vae_name": VIDEO_VAE}},
        "6": {"class_type": "VAELoader", "inputs": {"vae_name": AUDIO_VAE}},
        "8": {
            "class_type": "KSamplerSelect",
            "inputs": {"sampler_name": settings["sampler"]},
        },
        "10": {
            "class_type": "RandomNoise",
            "inputs": {"noise_seed": settings["seed"]},
        },
        "13": {
            "class_type": "LTXVSeparateAVLatent",
            "inputs": {"av_latent": ["12", 0]},
        },
        "14": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["13", 0], "vae": ["5", 0]},
        },
        "15": {
            "class_type": "VAEDecodeAudio",
            "inputs": {"samples": ["13", 1], "vae": ["6", 0]},
        },
        "16": {
            "class_type": "CreateVideo",
            "inputs": {"images": ["14", 0], "fps": 24.0, "audio": ["15", 0]},
        },
        "17": {
            "class_type": "SaveVideo",
            "inputs": {
                "video": ["16", 0],
                "filename_prefix": f"GENSO/{settings['out_name']}",
                "format": "auto",
                "codec": "auto",
            },
        },
    }

    model_link: list[Any] = ["1", 0]
    node_id = 2
    for lora in settings.get("loras", []):
        key = str(node_id)
        graph[key] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "model": model_link,
                "lora_name": lora["name"],
                "strength_model": lora["strength"],
            },
        }
        model_link = [key, 0]
        node_id = 30 if node_id == 2 else node_id + 1

    graph["3"] = {
        "class_type": "MiniMaxH3SigmaShift",
        "inputs": {
            "model": model_link,
            "shift_video": settings["shift_video"],
            "shift_audio": settings["shift_audio"],
        },
    }
    graph["9"] = {
        "class_type": "BasicScheduler",
        "inputs": {
            "model": ["3", 0],
            "scheduler": settings["scheduler"],
            "steps": settings["steps"],
            "denoise": 1.0,
        },
    }
    graph["11"] = {
        "class_type": "BasicGuider",
        "inputs": {"model": ["3", 0], "conditioning": ["7", 0]},
    }
    graph["12"] = {
        "class_type": "SamplerCustomAdvanced",
        "inputs": {
            "noise": ["10", 0],
            "guider": ["11", 0],
            "sampler": ["8", 0],
            "sigmas": ["9", 0],
            "latent_image": ["7", 1],
        },
    }

    conditioning_inputs: dict[str, Any] = {
        "clip": ["4", 0],
        "vae": ["5", 0],
        "prompt": settings["prompt"],
        "width": settings["width"],
        "height": settings["height"],
        "length": settings["length"],
    }

    if not is_ref:
        graph["7"] = {
            # The GGUF input embedding needs the same 3 GiB temporary work
            # buffer in both t2va and image-conditioned modes. Using the core
            # node for t2va bypassed GENSO's DynamicVRAM estimate and could OOM
            # before sampling even on a clean 8 GiB system.
            "class_type": "GensoH3ImageToVideo",
            "inputs": conditioning_inputs,
        }
        if settings.get("first_frame"):
            graph["21"] = {
                "class_type": "LoadImage",
                "inputs": {"image": settings["first_frame"]},
            }
            conditioning_inputs["first_frame"] = ["21", 0]
        if settings.get("last_frame"):
            graph["22"] = {
                "class_type": "LoadImage",
                "inputs": {"image": settings["last_frame"]},
            }
            conditioning_inputs["last_frame"] = ["22", 0]
        return graph

    conditioning_inputs.update(
        {"audio_vae": ["6", 0], "ref_image_size": settings["ref_image_size"]}
    )

    # Autogrow slots must be submitted as dot-paths ("ref_images.ref_image_1"),
    # never as a nested dict under "ref_images". ComfyUI looks the slot up with
    # finalize_prefix(["ref_images"], "ref_image_1") against the submitted inputs
    # (comfy_api/latest/_io.py: get_finalized_class_inputs -> Autogrow
    # ._expand_schema_for_dynamic), then build_nested_inputs splits on "." to
    # rebuild the dict the node's execute() expects.
    #
    # A nested dict is silently DISCARDED: it is not a declared input, so the run
    # succeeds with zero references and nothing reports a problem. This cost a
    # full day of measurements taken against generations that never saw their
    # reference image or audio at all (2026-08-17).
    for index, name in enumerate(settings.get("ref_images", [])):
        key = REF_IMAGE_NODE_IDS[index]
        graph[key] = {"class_type": "LoadImage", "inputs": {"image": name}}
        conditioning_inputs[f"ref_images.ref_image_{index + 1}"] = [key, 0]

    use_audio = settings.get("ref_video_use_audio", [])
    for index, name in enumerate(settings.get("ref_videos", [])):
        load_id, parts_id = REF_VIDEO_NODE_IDS[index]
        graph[load_id] = {"class_type": "LoadVideo", "inputs": {"file": name}}
        graph[parts_id] = {
            "class_type": "GetVideoComponents",
            "inputs": {"video": [load_id, 0]},
        }
        conditioning_inputs[f"ref_videos.ref_video_{index + 1}"] = [parts_id, 0]
        if index < len(use_audio) and use_audio[index]:
            conditioning_inputs[
                f"ref_video_audios.ref_video_audio_{index + 1}"
            ] = [parts_id, 1]

    for index, name in enumerate(settings.get("ref_audios", [])):
        key = REF_AUDIO_NODE_IDS[index]
        graph[key] = {"class_type": "LoadAudio", "inputs": {"audio": name}}
        conditioning_inputs[f"ref_audios.ref_audio_{index + 1}"] = [key, 0]

    graph["7"] = {
        "class_type": "GensoH3ReferenceToVideo",
        "inputs": conditioning_inputs,
    }

    # Reference mode has no first-frame input of its own, so identity drifts away
    # from the character sheet. Anchoring frame 0 fixes that without giving up
    # audio-driven lipsync; GensoH3AddKeyframe and the extra_conds patch in
    # nodes_override make the two conditioning kinds coexist.
    if settings.get("first_frame"):
        graph["21"] = {
            "class_type": "LoadImage",
            "inputs": {"image": settings["first_frame"]},
        }
        graph["22"] = {
            "class_type": "GensoH3AddKeyframe",
            "inputs": {
                "conditioning": ["7", 0],
                "vae": ["5", 0],
                "first_frame": ["21", 0],
                "width": settings["width"],
                "height": settings["height"],
                "length": settings["length"],
            },
        }
        graph["11"]["inputs"]["conditioning"] = ["22", 0]
    return graph

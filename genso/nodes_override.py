"""GENSO-only MiniMax H3 conditioning nodes for 8 GiB GPUs.

The core nodes remain untouched.  These subclasses reuse the core schemas and
implementations, adding only working-memory headroom for image-aware Qwen3-VL
encoding.  ComfyUI's dynamic loader otherwise fills almost all available VRAM
with text-encoder weights before the GGUF input embedding is expanded.
"""

from __future__ import annotations

from contextlib import contextmanager
import logging

import comfy.model_base
import comfy.model_management
import node_helpers
import nodes
from comfy_api.latest import io
from comfy_extras.nodes_minimax_h3 import (
    MiniMaxH3ImageToVideo,
    MiniMaxH3ReferenceToVideo,
    _empty_av_latent,
    _resize,
    align_frame_count,
)


GIB = 1024 ** 3
# Qwen3-VL-32B's 151k x 5120 input embedding needs a multi-GiB temporary
# expansion.  Reserving this space makes DynamicVRAM keep more TE weights on
# CPU and stream them as needed instead of failing at the first embedding.
CLIP_WORKING_MEMORY = 3 * GIB


def _vram_free_mib() -> int | None:
    device = comfy.model_management.get_torch_device()
    if getattr(device, "type", None) != "cuda":
        return None
    return round(comfy.model_management.get_free_memory(device) / (1024 ** 2))


@contextmanager
def _reserve_clip_working_memory(clip):
    """Temporarily teach the core CLIP loader about its GGUF work buffer."""

    stage_model = clip.cond_stage_model
    missing = object()
    previous = getattr(stage_model, "memory_estimation_function", missing)

    def estimate(tokens, device=None):
        prior = 0 if previous is missing else previous(tokens, device=device)
        return max(prior, CLIP_WORKING_MEMORY)

    stage_model.memory_estimation_function = estimate
    before = _vram_free_mib()
    logging.info(
        "GENSO MiniMax H3: reserving %.2f GiB TE working memory (VRAM free before load: %s MiB)",
        CLIP_WORKING_MEMORY / GIB,
        before if before is not None else "n/a",
    )
    try:
        yield
    finally:
        if previous is missing:
            delattr(stage_model, "memory_estimation_function")
        else:
            stage_model.memory_estimation_function = previous
        after = _vram_free_mib()
        logging.info(
            "GENSO MiniMax H3: conditioning complete (VRAM free: %s MiB)",
            after if after is not None else "n/a",
        )


class GensoH3ImageToVideo(MiniMaxH3ImageToVideo):
    """Core-compatible t2va/fl2va conditioning with 8 GiB TE headroom."""

    @classmethod
    def define_schema(cls):
        schema = super().define_schema()
        schema.node_id = "GensoH3ImageToVideo"
        schema.display_name = "GENSO H3 Video Conditioning (8GB)"
        schema.category = "GENSO/conditioning"
        schema.description = (
            "MiniMax H3 text/image/keyframe conditioning with explicit Qwen3-VL "
            "working-memory reservation for 8 GiB GPUs."
        )
        return schema

    @classmethod
    def execute(cls, clip, vae, prompt, width, height, length,
                first_frame=None, last_frame=None):
        with _reserve_clip_working_memory(clip):
            return super().execute(
                clip, vae, prompt, width, height, length,
                first_frame=first_frame, last_frame=last_frame,
            )


class GensoH3ReferenceToVideo(MiniMaxH3ReferenceToVideo):
    """Core-compatible ref2va conditioning with 8 GiB TE headroom."""

    @classmethod
    def define_schema(cls):
        schema = super().define_schema()
        schema.node_id = "GensoH3ReferenceToVideo"
        schema.display_name = "GENSO H3 Reference to Video (8GB)"
        schema.category = "GENSO/conditioning"
        schema.description = (
            "MiniMax H3 reference conditioning with explicit Qwen3-VL "
            "working-memory reservation for 8 GiB GPUs."
        )
        return schema

    @classmethod
    def execute(cls, clip, vae, audio_vae, prompt, width, height, length,
                ref_image_size="match", ref_images=None, ref_videos=None,
                ref_video_audios=None, ref_audios=None):
        # Fail loudly on a reference-less ref2va run. ComfyUI drops Autogrow
        # slots that were submitted in the wrong shape *silently*, so a graph
        # with a broken reference wiring generates a perfectly valid-looking
        # video that simply ignored the character sheet and the voice. That
        # went unnoticed for a full day of measurements (2026-08-17); the only
        # reliable defence is to refuse to run at all.
        if not any((ref_images, ref_videos, ref_audios)):
            raise ValueError(
                "参照して作るモードなのに参照が1つも届いていません。"
                "グラフの入力キーは 'ref_images.ref_image_1' のようなドット記法にしてください"
                "（'ref_images': {...} という入れ子は ComfyUI に無視されます）。"
            )
        with _reserve_clip_working_memory(clip):
            return super().execute(
                clip, vae, audio_vae, prompt, width, height, length,
                ref_image_size=ref_image_size,
                ref_images=ref_images,
                ref_videos=ref_videos,
                ref_video_audios=ref_video_audios,
                ref_audios=ref_audios,
            )


class GensoH3AddKeyframe(io.ComfyNode):
    """Pin the first frame of a ref2va generation to a supplied image.

    `ref_audios` (audio-driven lipsync) exists only on the reference node, and
    that node has no first-frame input, so identity used to drift: the outfit
    and background wandered away from the character sheet. The two H3
    checkpoints turned out to be structurally identical (932 tensors, matching
    shapes) and PackedLayout already lays out `keyframes` and `refs` as separate
    row ranges, so the exclusivity was never architectural — see
    _patch_extra_conds_for_keyframe_plus_refs below for the one line that
    blocked it.

    Measured: character held to the reference, mouth synced to the reference
    audio, 124 frames at 512x768 in 16.5 minutes on an 8 GiB card.
    """

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="GensoH3AddKeyframe",
            display_name="GENSO H3 Add Keyframe",
            category="GENSO/conditioning",
            description=(
                "Anchor frame 0 of a reference-mode generation to an image, so "
                "audio-driven lipsync and a fixed character can be used together."
            ),
            inputs=[
                io.Conditioning.Input("conditioning"),
                io.Vae.Input("vae"),
                io.Image.Input("first_frame"),
                io.Int.Input("width", default=512, min=32, max=nodes.MAX_RESOLUTION, step=32),
                io.Int.Input("height", default=768, min=32, max=nodes.MAX_RESOLUTION, step=32),
                io.Int.Input("length", default=124, min=5, max=3600),
            ],
            outputs=[io.Conditioning.Output(display_name="positive")],
        )

    @classmethod
    def execute(cls, conditioning, vae, first_frame, width, height, length):
        frame_count = align_frame_count(length)
        # Same treatment core fl2va gives a first frame: a plain stretch to the
        # target canvas ("geometry anchor"), then encode.
        image = _resize(first_frame[:1], width, height, "disabled")
        keyframes = [{"resolved_frame_index": 0, "image": image,
                      "latent": vae.encode(image)}]
        out = node_helpers.conditioning_set_values(conditioning, {
            "minimax_keyframes": keyframes,
            "minimax_frame_count": frame_count,
        })
        return io.NodeOutput(out)


def _patch_extra_conds_for_keyframe_plus_refs() -> None:
    """Let a keyframe and reference blocks share one generation.

    `MiniMaxH3.extra_conds` assigns `cond_video_latents` twice: the keyframe
    branch fills it, then the reference branch overwrites it. Meanwhile
    PackedLayout marks condition rows for *both*, so the row count no longer
    matches the latent count and

        all_video_rows[~img_update] = cond_video_rows

    fails on shape. Appending instead - in the order PackedLayout lays the rows
    out, keyframes first - is the whole fix.

    ComfyUI's own files are never modified; this rebinds the method at import.
    """
    if getattr(comfy.model_base.MiniMaxH3, "_genso_keyframe_refs_patched", False):
        return
    original = comfy.model_base.MiniMaxH3.extra_conds

    def extra_conds(self, **kwargs):
        out = original(self, **kwargs)
        payload_cond = out.get("minimax_payload")
        keyframes = kwargs.get("minimax_keyframes")
        refs = kwargs.get("minimax_refs")
        if payload_cond is None or not keyframes or not refs:
            return out          # single-mode runs behave exactly as before
        merged = [kf["latent"] for kf in keyframes]
        merged += [ref["latent"] for ref in refs if "latent" in ref]
        payload_cond.cond["cond_video_latents"] = merged
        return out

    comfy.model_base.MiniMaxH3.extra_conds = extra_conds
    comfy.model_base.MiniMaxH3._genso_keyframe_refs_patched = True
    logging.getLogger(__name__).info(
        "GENSO: keyframe + reference coexistence enabled")


_patch_extra_conds_for_keyframe_plus_refs()


# Keep imported helpers visible: these are deliberately reused from core rather
# than copied into this package (see design section 4.7).
__all__ = [
    "GensoH3ImageToVideo",
    "GensoH3ReferenceToVideo",
    "GensoH3AddKeyframe",
    "_empty_av_latent",
    "_resize",
]

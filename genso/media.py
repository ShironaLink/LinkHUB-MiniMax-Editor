"""Media probing and reference-video normalization using ComfyUI's PyAV."""

from __future__ import annotations

import math
import re
from fractions import Fraction
from pathlib import Path
from typing import Any

import av

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


def _duration(stream: Any, container: av.container.InputContainer) -> float:
    if stream.duration is not None and stream.time_base is not None:
        return max(0.0, float(stream.duration * stream.time_base))
    if container.duration is not None:
        return max(0.0, float(container.duration / av.time_base))
    return 0.0


def probe(path: str | Path) -> dict[str, Any]:
    """Return normalized metadata for an image, video, or audio file."""
    source = Path(path)
    with av.open(str(source)) as container:
        video = container.streams.video[0] if container.streams.video else None
        audio = container.streams.audio[0] if container.streams.audio else None
        if source.suffix.lower() in IMAGE_SUFFIXES:
            kind = "image"
        elif video is not None:
            kind = "video"
        elif audio is not None:
            kind = "audio"
        else:
            kind = "unknown"

        if video is not None:
            rate = video.average_rate or video.guessed_rate or Fraction(0, 1)
            duration = _duration(video, container)
            return {
                "kind": kind,
                "duration_sec": round(duration, 3),
                "fps": round(float(rate), 3) if rate else 0.0,
                "width": int(video.width or 0),
                "height": int(video.height or 0),
                "has_audio": audio is not None,
            }
        if audio is not None:
            return {
                "kind": kind,
                "duration_sec": round(_duration(audio, container), 3),
                "fps": 0.0,
                "width": 0,
                "height": 0,
                "has_audio": True,
            }
    return {"kind": "unknown", "duration_sec": 0.0, "fps": 0.0, "width": 0, "height": 0, "has_audio": False}


def _output_dimensions(width: int, height: int, target_width: int, target_height: int) -> tuple[int, int]:
    scale = min(1.0, target_width / width, target_height / height)
    out_width = max(2, int(math.floor(width * scale / 2.0) * 2))
    out_height = max(2, int(math.floor(height * scale / 2.0) * 2))
    return out_width, out_height


def _safe_stem(name: str) -> str:
    stem = Path(name).stem
    stem = re.sub(r"[^\w.-]+", "_", stem, flags=re.UNICODE).strip("._")
    return stem[:100] or "video"


def preprocess_reference_video(
    source: str | Path,
    input_dir: str | Path,
    original_name: str,
    target_width: int,
    target_height: int,
) -> dict[str, Any]:
    """Convert a reference to <= target canvas, exactly 24fps, and <=15s."""
    source = Path(source)
    input_dir = Path(input_dir)
    before = probe(source)
    if before["kind"] != "video" or not before["width"] or not before["height"]:
        raise ValueError("参照動画として読み取れる映像トラックがありません")

    out_width, out_height = _output_dimensions(
        before["width"], before["height"], target_width, target_height
    )
    output_name = f"genso_ref_{_safe_stem(original_name)}_{out_width}x{out_height}.mp4"
    destination = input_dir / output_name

    notes: list[str] = []
    if abs(before["fps"] - 24.0) > 0.01:
        notes.append("24fpsへ変換しました")
    if (out_width, out_height) != (before["width"], before["height"]):
        notes.append(f"{out_width}×{out_height}へ縮小しました")
    if before["duration_sec"] > 15.0:
        notes.append("先頭15秒にトリムしました")

    with av.open(str(source)) as source_container, av.open(str(destination), mode="w", format="mp4") as output:
        source_stream = source_container.streams.video[0]
        encoded = output.add_stream("libx264", rate=24)
        encoded.width = out_width
        encoded.height = out_height
        encoded.pix_fmt = "yuv420p"
        encoded.options = {"crf": "18", "preset": "medium"}
        encoded_audio = None
        if before.get("has_audio"):
            encoded_audio = output.add_stream("aac", rate=48000)
            encoded_audio.layout = "stereo"

        source_rate = float(source_stream.average_rate or source_stream.guessed_rate or 24)
        next_output_time = 0.0
        output_index = 0
        last_frame_time = 0.0
        for source_index, frame in enumerate(source_container.decode(source_stream)):
            frame_time = float(frame.time) if frame.time is not None else source_index / source_rate
            if frame_time >= 15.0:
                break
            last_frame_time = frame_time
            # Pick the nearest decoded frame at each 1/24s output instant. The
            # loop also duplicates a frame if the input cadence has a gap.
            while next_output_time <= frame_time + (0.5 / source_rate) and next_output_time < 15.0:
                converted = frame.reformat(width=out_width, height=out_height, format="yuv420p")
                converted.pts = output_index
                converted.time_base = Fraction(1, 24)
                for packet in encoded.encode(converted):
                    output.mux(packet)
                output_index += 1
                next_output_time = output_index / 24.0
        for packet in encoded.encode():
            output.mux(packet)

        # Decode audio in a second pass so video cadence conversion stays
        # simple and bounded. The graph decides whether this track is used.
        if encoded_audio is not None:
            with av.open(str(source)) as audio_container:
                source_audio = audio_container.streams.audio[0]
                resampler = av.AudioResampler(format="fltp", layout="stereo", rate=48000)
                audio_pts = 0
                limit = 15 * 48000
                for frame in audio_container.decode(source_audio):
                    if audio_pts >= limit:
                        break
                    for converted_audio in resampler.resample(frame):
                        converted_audio.pts = audio_pts
                        converted_audio.time_base = Fraction(1, 48000)
                        audio_pts += converted_audio.samples
                        for packet in encoded_audio.encode(converted_audio):
                            output.mux(packet)
                for converted_audio in resampler.resample(None):
                    if audio_pts >= limit:
                        break
                    converted_audio.pts = audio_pts
                    converted_audio.time_base = Fraction(1, 48000)
                    audio_pts += converted_audio.samples
                    for packet in encoded_audio.encode(converted_audio):
                        output.mux(packet)
                for packet in encoded_audio.encode():
                    output.mux(packet)

    if output_index == 0:
        destination.unlink(missing_ok=True)
        raise ValueError("参照動画からフレームを読み取れませんでした")

    after = probe(destination)
    if not notes:
        notes.append("変換不要のため映像を正規化しました")
    return {
        "ok": True,
        "name": output_name,
        "fps": 24,
        "duration_sec": after["duration_sec"] or round(min(last_frame_time, 15.0), 3),
        "width": out_width,
        "height": out_height,
        "has_audio": bool(after.get("has_audio")),
        "note": " / ".join(notes),
    }

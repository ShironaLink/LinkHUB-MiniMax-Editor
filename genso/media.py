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


# ---------------------------------------------------------------------------
# Chain support (RENSO / 連創型)
#
# A chain hands one generation's tail to the next generation's head, so the two
# things it needs are "read the very last frame out of an mp4" and "put the
# finished clips back together". Both stay inside PyAV: GENSO deliberately has
# no ffmpeg binary to depend on.
# ---------------------------------------------------------------------------

# H3's video VAE returns a picture slightly darker than it was given. Measured
# on the 素体ちゃん smile pair (2026-09-03): the decoded clip sits at roughly
# -2.5 / -3.9 / -4.5 (R/G/B) against its own keyframe. Feeding a tail frame back
# in as the next head would compound that sink once per segment, so the chain
# can add it back before handing the frame on. The numbers come from a single
# measurement, so renso keeps the correction switchable and reports the real
# per-segment means next to it.
VAE_COLOR_OFFSET = (2.5, 3.9, 4.5)

AUDIO_RATE = 48000
AUDIO_LAYOUT = "stereo"


def _decode_last_frame(container: Any, stream: Any) -> Any:
    """Return the final decoded video frame, or None for an empty stream.

    Seeking near the end and decoding forward would be faster, but a chain clip
    is at most 362 frames and a missed keyframe here would silently hand the
    wrong picture to the next segment. Decoding straight through is cheap enough
    to be worth the certainty.
    """
    last = None
    for frame in container.decode(stream):
        last = frame
    return last


def extract_last_frame(
    video: str | Path,
    destination: str | Path,
    color_offset: tuple[float, float, float] | None = None,
) -> dict[str, Any]:
    """Write the final frame of `video` to `destination` as a PNG.

    `color_offset` is added per channel before saving; pass VAE_COLOR_OFFSET to
    undo the decoder sink, or None to store the frame exactly as generated.
    """
    import numpy as np
    from PIL import Image

    video = Path(video)
    destination = Path(destination)
    with av.open(str(video)) as container:
        if not container.streams.video:
            raise ValueError("映像トラックがありません")
        stream = container.streams.video[0]
        frame = _decode_last_frame(container, stream)
        if frame is None:
            raise ValueError("最終フレームを読み取れませんでした")
        pixels = frame.to_ndarray(format="rgb24")

    before = [round(float(pixels[:, :, channel].mean()), 3) for channel in range(3)]
    if color_offset:
        corrected = pixels.astype(np.float32)
        for channel, delta in enumerate(color_offset[:3]):
            corrected[:, :, channel] += float(delta)
        pixels = np.clip(corrected, 0, 255).astype(np.uint8)
    after = [round(float(pixels[:, :, channel].mean()), 3) for channel in range(3)]

    destination.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(pixels, mode="RGB").save(str(destination), format="PNG")
    return {
        "name": destination.name,
        "width": int(pixels.shape[1]),
        "height": int(pixels.shape[0]),
        "mean_rgb": before,
        "mean_rgb_corrected": after,
        "corrected": bool(color_offset),
    }


def extract_last_frames(
    video: str | Path,
    destinations: list[str | Path],
    color_offset: tuple[float, float, float] | None = None,
) -> dict[str, Any]:
    """Write the final len(destinations) frames of `video` as PNGs, oldest first.

    The continuation-clip form of extract_last_frame: RENSO anchors these frames
    at the head of the next segment so it inherits motion and line weight, not
    just one picture. The same colour offset is applied to every frame.

    Example: extract_last_frames("seg.mp4", ["t1.png", ..., "t5.png"]) writes
    frames 51..55 of a 56-frame clip; t5.png is the clip's very last frame.
    """
    from collections import deque

    import numpy as np
    from PIL import Image

    count = len(destinations)
    if count < 1:
        raise ValueError("書き出すフレーム数が0です")
    video = Path(video)
    with av.open(str(video)) as container:
        if not container.streams.video:
            raise ValueError("映像トラックがありません")
        stream = container.streams.video[0]
        # Decode straight through for the same reason _decode_last_frame does.
        window = deque(
            (frame.to_ndarray(format="rgb24") for frame in container.decode(stream)),
            maxlen=count,
        )
    if len(window) < count:
        raise ValueError(f"末尾{count}フレームを読み取れませんでした（{len(window)}フレームのみ）")

    names = []
    for pixels, destination in zip(window, destinations):
        if color_offset:
            corrected = pixels.astype(np.float32)
            for channel, delta in enumerate(color_offset[:3]):
                corrected[:, :, channel] += float(delta)
            pixels = np.clip(corrected, 0, 255).astype(np.uint8)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(pixels, mode="RGB").save(str(destination), format="PNG")
        names.append(destination.name)
    last = window[-1]
    return {
        "names": names,
        "width": int(last.shape[1]),
        "height": int(last.shape[0]),
        "corrected": bool(color_offset),
    }


def mean_rgb(video: str | Path, sample_points: int = 5) -> list[float]:
    """Average R/G/B over evenly spaced frames, so colour drift stays visible."""
    import numpy as np

    with av.open(str(video)) as container:
        if not container.streams.video:
            raise ValueError("映像トラックがありません")
        stream = container.streams.video[0]
        total = int(stream.frames or 0)
        if not total:
            fps = float(stream.average_rate or stream.guessed_rate or 24)
            duration = float(stream.duration * stream.time_base) if stream.duration else 0.0
            total = max(1, round(duration * fps))
        wanted = set(
            np.linspace(0, max(0, total - 1), max(1, sample_points)).round().astype(int).tolist()
        )
        sums = np.zeros(3, dtype=np.float64)
        counted = 0
        for index, frame in enumerate(container.decode(stream)):
            if index not in wanted:
                continue
            pixels = frame.to_ndarray(format="rgb24")
            sums += pixels.reshape(-1, 3).mean(axis=0)
            counted += 1
    if not counted:
        raise ValueError("映像フレームを読み取れませんでした")
    return [round(float(value), 3) for value in sums / counted]


def _trim_audio_head(frame: Any, remaining: int) -> tuple[Any | None, int]:
    """Drop `remaining` samples from the front of a resampled audio frame."""
    if remaining <= 0:
        return frame, 0
    if frame.samples <= remaining:
        return None, remaining - frame.samples
    import numpy as np

    values = frame.to_ndarray()[:, remaining:]
    trimmed = av.AudioFrame.from_ndarray(
        np.ascontiguousarray(values), format=frame.format.name, layout=frame.layout.name
    )
    trimmed.sample_rate = frame.sample_rate
    return trimmed, 0


def concat_videos(
    sources: list[str | Path],
    destination: str | Path,
    drop_joint_duplicate: bool = True,
    joint_drops: list[int] | None = None,
) -> dict[str, Any]:
    """Join chain segments into one mp4, re-encoded to the first clip canvas.

    Neighbouring segments share a frame by construction — B is segment 0's last
    frame and segment 1's first — so `drop_joint_duplicate` skips frame 0 of
    every clip after the first. The matching 1/24s of audio is skipped with it;
    dropping only the picture would walk the sound 42ms further out of sync at
    every joint.

    `joint_drops` overrides the count per clip (index-aligned with `sources`;
    entry 0 is ignored): a segment that was anchored on the previous segment's
    last 5 frames repeats all 5, so its entry is 5. Audio is trimmed by the same
    number of 1/24s slices. drop_joint_duplicate=False still drops nothing.
    """
    paths = [Path(item) for item in sources]
    if not paths:
        raise ValueError("連結する動画がありません")
    for path in paths:
        if not path.is_file():
            raise ValueError(f"連結できない動画があります: {path.name}")

    first = probe(paths[0])
    width = int(first["width"])
    height = int(first["height"])
    if not width or not height:
        raise ValueError("連結元の解像度を読み取れません")
    resized = [
        path.name
        for path in paths[1:]
        if (probe(path)["width"], probe(path)["height"]) != (width, height)
    ]

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    samples_per_frame = AUDIO_RATE // 24
    video_index = 0
    audio_pts = 0

    with av.open(str(destination), mode="w", format="mp4") as output:
        encoded = output.add_stream("libx264", rate=24)
        encoded.width = width
        encoded.height = height
        encoded.pix_fmt = "yuv420p"
        encoded.options = {"crf": "17", "preset": "medium"}
        encoded_audio = output.add_stream("aac", rate=AUDIO_RATE)
        encoded_audio.layout = AUDIO_LAYOUT

        for order, path in enumerate(paths):
            skip_count = 0
            if drop_joint_duplicate and order > 0:
                skip_count = 1
                if joint_drops is not None and order < len(joint_drops):
                    skip_count = max(0, int(joint_drops[order]))
            with av.open(str(path)) as container:
                if not container.streams.video:
                    raise ValueError(f"映像トラックがありません: {path.name}")
                stream = container.streams.video[0]
                for index, frame in enumerate(container.decode(stream)):
                    if index < skip_count:
                        continue
                    converted = frame.reformat(width=width, height=height, format="yuv420p")
                    converted.pts = video_index
                    converted.time_base = Fraction(1, 24)
                    for packet in encoded.encode(converted):
                        output.mux(packet)
                    video_index += 1

            with av.open(str(path)) as container:
                if not container.streams.audio:
                    continue
                source_audio = container.streams.audio[0]
                resampler = av.AudioResampler(
                    format="fltp", layout=AUDIO_LAYOUT, rate=AUDIO_RATE
                )
                remaining_skip = samples_per_frame * skip_count
                for frame in container.decode(source_audio):
                    for converted_audio in resampler.resample(frame):
                        converted_audio, remaining_skip = _trim_audio_head(
                            converted_audio, remaining_skip
                        )
                        if converted_audio is None:
                            continue
                        converted_audio.pts = audio_pts
                        converted_audio.time_base = Fraction(1, AUDIO_RATE)
                        audio_pts += converted_audio.samples
                        for packet in encoded_audio.encode(converted_audio):
                            output.mux(packet)
                for converted_audio in resampler.resample(None):
                    converted_audio, remaining_skip = _trim_audio_head(
                        converted_audio, remaining_skip
                    )
                    if converted_audio is None:
                        continue
                    converted_audio.pts = audio_pts
                    converted_audio.time_base = Fraction(1, AUDIO_RATE)
                    audio_pts += converted_audio.samples
                    for packet in encoded_audio.encode(converted_audio):
                        output.mux(packet)

        for packet in encoded.encode():
            output.mux(packet)
        for packet in encoded_audio.encode():
            output.mux(packet)

    if video_index == 0:
        destination.unlink(missing_ok=True)
        raise ValueError("連結できるフレームがありませんでした")

    notes: list[str] = []
    if drop_joint_duplicate and len(paths) > 1:
        dropped = sum(
            (max(0, int(joint_drops[order])) if joint_drops is not None and order < len(joint_drops) else 1)
            for order in range(1, len(paths))
        )
        notes.append(f"継ぎ目の重複フレームを{dropped}枚除きました")
    if resized:
        notes.append(f"解像度の違う{len(resized)}本を{width}×{height}へ合わせました")
    return {
        "name": destination.name,
        "frames": video_index,
        "duration_sec": round(video_index / 24, 3),
        "width": width,
        "height": height,
        "note": " / ".join(notes) or "そのまま連結しました",
    }

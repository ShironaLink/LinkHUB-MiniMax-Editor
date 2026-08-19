"""Quality-control checks for generated MiniMax-H3 MP4 files."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import av
import numpy as np

SAMPLE_POINTS = 5


def _video_stats(path: Path) -> dict[str, Any]:
    with av.open(str(path)) as container:
        if not container.streams.video:
            raise ValueError("映像トラックがありません")
        stream = container.streams.video[0]
        fps = float(stream.average_rate or stream.guessed_rate or 24)
        estimated_frames = int(stream.frames or 0)
        if not estimated_frames:
            duration = float(stream.duration * stream.time_base) if stream.duration else 0.0
            estimated_frames = max(1, round(duration * fps))
        targets = set(np.linspace(0, max(0, estimated_frames - 1), SAMPLE_POINTS).round().astype(int).tolist())
        samples: list[dict[str, Any]] = []
        last: tuple[int, np.ndarray] | None = None
        for index, frame in enumerate(container.decode(stream)):
            pixels = None
            if index in targets or index >= estimated_frames - 1:
                pixels = frame.to_ndarray(format="rgb24")
                last = (index, pixels)
            if index in targets and pixels is not None:
                samples.append(_pixel_stats(index, pixels))
        if not samples and last is not None:
            samples.append(_pixel_stats(*last))
        elif last is not None and samples[-1]["frame"] != last[0]:
            samples.append(_pixel_stats(*last))
        if not samples:
            raise ValueError("映像フレームを読み取れませんでした")
        return {
            "width": int(stream.width or 0),
            "height": int(stream.height or 0),
            "fps": round(fps, 3),
            "sampled": samples[:SAMPLE_POINTS],
        }


def _pixel_stats(index: int, pixels: np.ndarray) -> dict[str, Any]:
    values = pixels.reshape(-1)
    return {
        "frame": index,
        "mean": round(float(values.mean()), 4),
        "std": round(float(values.std()), 4),
        "min": int(values.min()),
        "max": int(values.max()),
        "unique": int(np.unique(values).size),
        "nan": int(np.isnan(values).sum()),
    }


def _audio_stats(path: Path) -> dict[str, Any]:
    sum_squares = 0.0
    sample_count = 0
    peak = 0.0
    nan_count = 0
    with av.open(str(path)) as container:
        if not container.streams.audio:
            return {"present": False, "samples": 0, "rms": 0.0, "peak": 0.0, "nan": 0}
        stream = container.streams.audio[0]
        for frame in container.decode(stream):
            values = frame.to_ndarray()
            original_dtype = values.dtype
            values = values.astype(np.float64, copy=False)
            if np.issubdtype(original_dtype, np.integer):
                values /= max(abs(np.iinfo(original_dtype).min), np.iinfo(original_dtype).max)
            nan_count += int(np.isnan(values).sum())
            finite = values[np.isfinite(values)]
            if finite.size:
                sum_squares += float(np.square(finite).sum())
                sample_count += int(finite.size)
                peak = max(peak, float(np.abs(finite).max()))
    rms = math.sqrt(sum_squares / sample_count) if sample_count else 0.0
    return {
        "present": True,
        "samples": sample_count,
        "rms": round(rms, 8),
        "peak": round(peak, 8),
        "nan": nan_count,
    }


ENVELOPE_HOP_MS = 20
MAX_LAG_STEPS = 100                 # +-2 s at 20 ms
REFERENCE_APPLIED_R = 0.9


def _mono_envelope(path: Path) -> tuple[np.ndarray, int]:
    """Loudness over time, normalised. Timing survives here; timbre does not."""
    chunks: list[np.ndarray] = []
    rate = 0
    with av.open(str(path)) as container:
        if not container.streams.audio:
            return np.zeros(0), 0
        stream = container.streams.audio[0]
        rate = int(stream.rate or 0)
        for frame in container.decode(stream):
            values = frame.to_ndarray().astype(np.float64, copy=False)
            if values.ndim > 1:
                values = values.mean(axis=0)
            chunks.append(values)
    if not chunks or not rate:
        return np.zeros(0), 0
    signal = np.concatenate(chunks)
    if np.abs(signal).max() > 1.5:          # integer PCM arrived unscaled
        signal = signal / 32768.0
    hop = max(1, int(rate * ENVELOPE_HOP_MS / 1000))
    count = len(signal) // hop
    if count < 2:
        return np.zeros(0), rate
    values = np.sqrt(np.array([(signal[i*hop:(i+1)*hop] ** 2).mean()
                               for i in range(count)]) + 1e-12)
    return values / (values.max() + 1e-9), rate


def compare_reference_audio(video: str | Path, reference: str | Path) -> dict[str, Any]:
    """Did the reference audio actually reach the model, and by how much is it offset?

    ComfyUI silently drops Autogrow inputs submitted in the wrong shape, so a
    reference-mode run can complete having ignored the voice entirely. The two
    cases are easy to tell apart once measured: a wired run reproduces the
    supplied track closely (r around 0.98), while an unwired one only reaches
    ~0.72 because it is reading the prompt's dialogue rather than the audio.

    The lag matters too. When the clip length was aligned to the audio it
    measured 0 ms and the canonical take could be dropped straight back in; a
    clip whose length was not aligned came out 300 ms adrift.
    """
    generated, _ = _mono_envelope(Path(video))
    supplied, _ = _mono_envelope(Path(reference))
    if generated.size < 40 or supplied.size < 40:
        return {"available": False}

    best_r, best_lag = -2.0, 0
    for lag in range(-MAX_LAG_STEPS, MAX_LAG_STEPS + 1):
        left = generated[max(0, lag):]
        right = supplied[max(0, -lag):]
        size = min(len(left), len(right))
        if size < 40:
            continue
        left, right = left[:size], right[:size]
        if left.std() < 1e-6 or right.std() < 1e-6:
            continue
        score = float(np.corrcoef(left, right)[0, 1])
        if score > best_r:
            best_r, best_lag = score, lag
    return {
        "available": True,
        "correlation": round(best_r, 4),
        "lag_ms": best_lag * ENVELOPE_HOP_MS,
        "references_applied": best_r >= REFERENCE_APPLIED_R,
    }


def inspect_video(path: str | Path, reference_audio: str | Path | None = None) -> dict[str, Any]:
    target = Path(path)
    video = _video_stats(target)
    audio = _audio_stats(target)
    sampled = video["sampled"]
    has_nan = any(item["nan"] for item in sampled) or audio["nan"] > 0
    is_black = all(item["mean"] < 2.0 and item["unique"] <= 32 for item in sampled)
    is_flat = all(item["std"] < 3.0 for item in sampled)
    is_white = all(item["mean"] > 247.0 for item in sampled)
    is_silent = audio["present"] and audio["samples"] > 0 and audio["peak"] < 1e-4

    sync: dict[str, Any] = {"available": False}
    if reference_audio:
        try:
            sync = compare_reference_audio(target, reference_audio)
        except Exception:
            sync = {"available": False}

    if has_nan:
        verdict = "NAN"
    elif is_black:
        verdict = "BLACK"
    elif is_white or is_flat:
        verdict = "SUSPECT"
    elif is_silent:
        verdict = "SILENT"
    elif sync.get("available") and not sync.get("references_applied"):
        # The clip is fine as a video but ignored the voice it was given, which
        # is exactly the failure that used to pass unnoticed.
        verdict = "NOREF"
    else:
        verdict = "OK"
    return {"verdict": verdict,
            "stats": {"video": video, "audio": audio, "reference_sync": sync}}


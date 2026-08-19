"""Resource preflight rules kept independent from ComfyUI imports."""

from __future__ import annotations


# Idle VRAM reported by ComfyUI fluctuates by roughly 0.1 GiB as WebView2 and
# the Windows compositor allocate surfaces. The previous 6.9 GiB hard cutoff
# rejected a clean 8 GiB system at 6.89 GiB. A loaded Ollama model reduces free
# VRAM by several GiB, so 6.5 GiB still catches the actual conflict with ample
# separation while allowing normal desktop fluctuation.
MIN_GENERATION_VRAM_GIB = 6.5


def generation_vram_error(free_gib: float) -> str | None:
    if free_gib >= MIN_GENERATION_VRAM_GIB:
        return None
    return (
        "別の生成モデルがVRAMを使用している可能性があります。"
        f"現在の空き {free_gib:.2f} GiB / 必要 {MIN_GENERATION_VRAM_GIB:.1f} GiB以上。"
        "Ollama翻訳の終了を待つか、他の生成アプリを閉じてください"
    )

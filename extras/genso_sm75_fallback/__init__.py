"""Route comfy_kitchen away from its CUDA kernels on pre-sm_80 GPUs.

Why this exists
---------------
ComfyUI 0.37.0 ships comfy-kitchen 0.2.35, whose native CUDA kernels are built
for sm_80 and newer. Its own source says so:

    backends/cuda/__init__.py:312    supported = major >= 8
    backends/cuda/__init__.py:2608   "sub-sm_80 cubins return garbage"

but the backend still registers itself as *available* on this machine's RTX
2080 SUPER (sm_75) and advertises every capability. The registry then hands it
work it cannot run, and the kernel launch fails:

    quantize_int8_rowwise  -> RuntimeError: CUDA INT8 rowwise quantization
                              failed: invalid argument      (int8 DiT, sampling)
    rms_rope_split_half_   -> RuntimeError: CUDA error: invalid argument
                              (video VAE decode, hit with the GGUF DiT after
                               all 8 sampling steps had completed)

Both MiniMax-H3 paths die on this: int8_convrot at the first sampling step, and
GGUF Q3_K_M at VAE decode. Neither is a model or workflow problem.

What it does
------------
On a GPU with compute capability below 8.0 it disables the "cuda" backend in
comfy_kitchen's registry. The same operations then resolve to the "eager"
backend, which is plain PyTorch and works on Turing. `int8_linear`,
`rms_rope_split_half_` and `quantize_int8_rowwise` all exist there.

This touches no ComfyUI file. Deleting this folder restores the stock
behaviour exactly.

Trade-off
---------
Eager is pure PyTorch, so it is slower than a working native kernel would be.
That is not a real loss here: the native kernels do not run on this card at
all. If comfy-kitchen later gates itself properly, or the card is replaced with
an sm_80+ GPU, delete this folder.
"""

import logging

NODE_CLASS_MAPPINGS: dict = {}
NODE_DISPLAY_NAME_MAPPINGS: dict = {}

MIN_NATIVE_MAJOR = 8


def _apply() -> None:
    try:
        import torch
    except Exception:
        return
    if not torch.cuda.is_available():
        return

    try:
        major, minor = torch.cuda.get_device_capability()
    except Exception as exc:
        logging.warning("[sm75-fallback] could not read compute capability: %s", exc)
        return
    if major >= MIN_NATIVE_MAJOR:
        return

    try:
        import comfy_kitchen
    except Exception:
        # No comfy_kitchen (older ComfyUI): nothing to route around.
        return

    try:
        comfy_kitchen.disable_backend("cuda")
    except Exception as exc:
        logging.warning(
            "[sm75-fallback] comfy_kitchen backend could not be disabled: %s", exc
        )
        return

    logging.info(
        "[sm75-fallback] sm_%d%d is below sm_%d0: comfy_kitchen CUDA kernels "
        "disabled, using the eager (PyTorch) backend",
        major, minor, MIN_NATIVE_MAJOR,
    )


_apply()

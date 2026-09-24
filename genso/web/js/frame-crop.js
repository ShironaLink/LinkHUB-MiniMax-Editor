/* Shared, local-only keyframe preparation for GENSO and RENSO. */
(() => {
  let busy = false;
  const clamp = (n, lo, hi) => Math.max(lo, Math.min(hi, n));
  function cropRect(sw, sh, width, height, zoom, x, y) {
    const scale = Math.max(width / sw, height / sh) * zoom;
    const w = width / scale, h = height / scale;
    return { x: (sw - w) * x, y: (sh - h) * y, w, h };
  }
  async function prepare(file, width, height, { force = false } = {}) {
    if (busy) throw new Error("開いているトリミングを確定またはキャンセルしてください");
    if (![width, height].every(n => Number.isInteger(n) && n >= 32 && n <= 8192)) {
      throw new Error("先に有効な動画の幅と高さを指定してください");
    }
    busy = true;
    const url = URL.createObjectURL(file), image = new Image();
    let dialog;
    try {
      await new Promise((resolve, reject) => {
        image.onload = resolve;
        image.onerror = () => reject(new Error("画像を読み込めませんでした"));
        image.src = url;
      });
      const sw = image.naturalWidth, sh = image.naturalHeight;
      let zoom = 1, x = .5, y = .5;
      if (force || sw * height !== sh * width) {
        dialog = document.createElement("dialog");
        dialog.className = "genso-crop-dialog";
        dialog.setAttribute("aria-labelledby", "genso-crop-title");
        dialog.innerHTML = `
          <style>
            .genso-crop-dialog{color:#eee;background:#16191f;border:1px solid #69717e;border-radius:14px;padding:24px;max-width:min(850px,94vw);max-height:94vh;overflow:auto;box-sizing:border-box}
            .genso-crop-dialog::backdrop{background:#000b}
            .genso-crop-dialog h2{margin:0 0 10px;font-size:21px}
            .genso-crop-dialog p{line-height:1.6;font-size:14px}
            .genso-crop-dialog canvas{display:block;max-width:100%;height:auto;margin:12px auto;background:#222;cursor:grab;touch-action:none;border:1px solid #9aa6b6}
            .genso-crop-dialog label{display:flex;align-items:center;gap:12px;margin:12px 0}
            .genso-crop-dialog input{flex:1;min-width:60px}
            .genso-crop-actions{display:flex;justify-content:flex-end;flex-wrap:wrap;gap:12px;margin-top:20px}
            .genso-crop-dialog button{padding:10px 18px;border-radius:8px;border:1px solid #69717e;background:#29303b;color:white;cursor:pointer}
            .genso-crop-dialog button[data-crop=ok]{background:#1761b0}
          </style>
          <h2 id="genso-crop-title">動画に使う範囲を選択</h2>
          <p>元画像 ${sw}×${sh} → 動画 ${width}×${height}<br>枠内が動画に使われます。画像をドラッグして位置を調整できます。</p>
          <canvas aria-label="切り抜きプレビュー"></canvas>
          <label>拡大 <input aria-label="拡大" data-crop="zoom" type="range" min="1" max="3" step="0.01" value="1"></label>
          <label>左右 <input aria-label="左右" data-crop="x" type="range" min="0" max="1" step="0.001" value="0.5"></label>
          <label>上下 <input aria-label="上下" data-crop="y" type="range" min="0" max="1" step="0.001" value="0.5"></label>
          <div class="genso-crop-actions"><button data-crop="reset">中央に戻す</button><button data-crop="cancel">キャンセル</button><button data-crop="ok" autofocus>この範囲を使う</button></div>`;
        document.body.append(dialog);
        const find = key => dialog.querySelector(`[data-crop="${key}"]`);
        const canvas = dialog.querySelector("canvas"), ctx = canvas.getContext("2d");
        const previewScale = Math.min(760 / width, 400 / height, 1);
        canvas.width = Math.round(width * previewScale);
        canvas.height = Math.round(height * previewScale);
        function draw() {
          const r = cropRect(sw, sh, width, height, zoom, x, y);
          ctx.clearRect(0, 0, canvas.width, canvas.height);
          ctx.drawImage(image, r.x, r.y, r.w, r.h, 0, 0, canvas.width, canvas.height);
          find("x").disabled = Math.abs(sw - r.w) < .01;
          find("y").disabled = Math.abs(sh - r.h) < .01;
          find("x").value = x; find("y").value = y; find("zoom").value = zoom;
        }
        for (const key of ["zoom", "x", "y"]) find(key).addEventListener("input", () => {
          zoom = Number(find("zoom").value); x = Number(find("x").value); y = Number(find("y").value); draw();
        });
        find("reset").onclick = () => { zoom = 1; x = y = .5; draw(); };
        let drag;
        canvas.onpointerdown = e => { drag = { px: e.clientX, py: e.clientY, x, y }; canvas.setPointerCapture(e.pointerId); };
        canvas.onpointermove = e => {
          if (!drag) return;
          const r = cropRect(sw, sh, width, height, zoom, x, y), box = canvas.getBoundingClientRect();
          if (sw - r.w > .01) x = clamp(drag.x - (e.clientX - drag.px) * r.w / box.width / (sw - r.w), 0, 1);
          if (sh - r.h > .01) y = clamp(drag.y - (e.clientY - drag.py) * r.h / box.height / (sh - r.h), 0, 1);
          draw();
        };
        canvas.onpointerup = canvas.onpointercancel = () => { drag = null; };
        const accepted = await new Promise(resolve => {
          find("cancel").onclick = () => resolve(false);
          find("ok").onclick = () => resolve(true);
          dialog.addEventListener("cancel", e => { e.preventDefault(); resolve(false); });
          dialog.showModal(); draw();
        });
        if (!accepted) return null;
      }
      const output = document.createElement("canvas");
      output.width = width; output.height = height;
      const r = cropRect(sw, sh, width, height, zoom, x, y);
      const ctx = output.getContext("2d");
      ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = "high";
      ctx.drawImage(image, r.x, r.y, r.w, r.h, 0, 0, width, height);
      const blob = await new Promise(resolve => output.toBlob(resolve, "image/png"));
      if (!blob) throw new Error("切り抜き画像を保存できませんでした");
      const name = `frame_${width}x${height}_${crypto.randomUUID()}.png`;
      return new File([blob], name, { type: "image/png" });
    } finally {
      if (dialog) { dialog.close(); dialog.remove(); }
      URL.revokeObjectURL(url); busy = false;
    }
  }
  window.GensoFrameCrop = { prepare, cropRect };
})();

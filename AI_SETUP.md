# AI_SETUP.md — AIエージェント向けセットアップ手順

このファイルは、利用者の代わりに **AIエージェント（Claude Code / Codex など）がこのアプリを導入する**ための手順書です。
人間が読む説明は [README.md](README.md) にあります。上から順に実行し、各ステップの「確認」が通ってから次へ進んでください。

対象: **LinkHUB 零式 MINI MAX簡易エディタ（GENSO）** — MiniMax-H3（映像＋音声の同時生成モデル）を ComfyUI 経由でフォーム操作だけで使う Windows アプリ。

---

## 0. エージェントへの約束事（必ず守る）

- **Windows 専用**です。macOS / Linux では動きません。
- **ComfyUI 本体のファイルは書き換えない。** このアプリは `custom_nodes` にジャンクション（リンク）を1つ作るだけで動きます。
- **大きなダウンロード（モデル合計 約40GB）は、始める前に利用者の了承を取る。** 保存先ドライブの空き容量も先に確認する。
- パス（ComfyUI の場所・モデルの置き場所）は**推測せず利用者に確認する**。
- 既存の ComfyUI 環境に `pip install` するのは、このファイルで指示された場合だけ。その場合も ComfyUI 自身の venv の python を使う（グローバルの python は使わない）。
- 失敗したら推測で直さず、`engine.log`（このリポジトリ直下に作られる）の末尾を読んで原因を特定する。

---

## 1. 環境チェック

```powershell
nvidia-smi --query-gpu=name,memory.total,driver_version,compute_cap --format=csv
py -V:3.12 --version
```

| 項目 | 条件 |
|---|---|
| GPU | NVIDIA 製、**VRAM 8GB 以上** |
| compute_cap | 7.5（RTX 20 シリーズ）なら **手順4が必須** |
| Python | `py -V:3.12` が動くこと（アプリの窓用。ComfyUI の Python とは別） |
| 空き容量 | モデル置き場に **70GB 以上** |
| RAM | 32GB 以上を推奨（モデルの大半がシステムメモリに載るため） |

**確認:** 上の2コマンドが両方とも結果を返すこと。

---

## 2. ComfyUI を用意する

- 動作確認済みバージョン: **ComfyUI 0.37.0**。MiniMax-H3 のノードが本体に入っている版が必要です。
- アプリは ComfyUI を `<ComfyUIフォルダ>\venv\Scripts\python.exe` で起動します。**この場所に venv がある構成が必要**です（[Stability Matrix](https://lykos.ai/) で入れた ComfyUI はこの構成）。

利用者に ComfyUI フォルダ（`main.py` があるフォルダ）の場所を聞き、次を確認します。

```powershell
$C = "<ComfyUIフォルダ>"
Test-Path "$C\main.py"
Test-Path "$C\venv\Scripts\python.exe"
Test-Path "$C\comfy_extras\nodes_minimax_h3.py"
& "$C\venv\Scripts\python.exe" -c "import comfyui_version; print(comfyui_version.__version__)"
```

**確認:** 3つとも `True`、バージョンが 0.37.0 以上。`nodes_minimax_h3.py` が無い場合は ComfyUI の更新が必要（利用者に確認してから）。

---

## 3. ComfyUI-GGUF を入れる（必須）

テキストエンコーダーが GGUF 形式のため、[city96/ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF) が必要です。

```powershell
$C = "<ComfyUIフォルダ>"
if (-not (Test-Path "$C\custom_nodes\ComfyUI-GGUF")) {
  git clone https://github.com/city96/ComfyUI-GGUF "$C\custom_nodes\ComfyUI-GGUF"
  & "$C\venv\Scripts\python.exe" -m pip install -r "$C\custom_nodes\ComfyUI-GGUF\requirements.txt"
}
```

**確認:** `$C\custom_nodes\ComfyUI-GGUF\__init__.py` が存在する。

---

## 4. RTX 20 シリーズ（compute_cap 7.5）の場合のみ

ComfyUI 0.37.0 に同梱の comfy-kitchen は、ネイティブ CUDA カーネルが sm_80 以上専用です。sm_75 では **MiniMax-H3 がどの設定でも生成途中で落ちます**（`CUDA INT8 rowwise quantization failed: invalid argument` など）。
このリポジトリの `extras\genso_sm75_fallback` を ComfyUI の `custom_nodes` へコピーすると、該当処理が純 PyTorch 実装へ切り替わり動くようになります。sm_80 以上の GPU では何もしないので、入れておいても害はありません。

```powershell
Copy-Item -Recurse "<このリポジトリ>\extras\genso_sm75_fallback" "<ComfyUIフォルダ>\custom_nodes\genso_sm75_fallback"
```

**確認:** 手順7でエンジン起動後、`engine.log` に `[sm75-fallback] ... using the eager (PyTorch) backend` が出る。

---

## 5. モデルを配置する（利用者の了承を取ってから）

置き場所は `<ComfyUIフォルダ>\models\` 配下です（Stability Matrix で共有モデルフォルダを使っている場合は、その対応フォルダ）。

| 役割 | ファイル | 配置先 | 入手先 |
|---|---|---|---|
| 映像モデル（画像から） | `minimax_h3_fl2va_pruned_int8_convrot.safetensors` | `diffusion_models` | https://huggingface.co/Comfy-Org/MiniMax-H3 |
| 映像モデル（参照から） | `minimax_h3_ref2va_pruned_int8_convrot.safetensors` | `diffusion_models` | https://huggingface.co/Comfy-Org/MiniMax-H3 |
| テキストエンコーダー | `qwen3vl_32b_minimax_h3-Q4_K_M.gguf` | `text_encoders` | https://huggingface.co/Abiray/MiniMax-H3-GGUF |
| 映像VAE | `minimax_h3_video_vae_fp16.safetensors` | `vae` | https://huggingface.co/Comfy-Org/MiniMax-H3 |
| 音声VAE | `minimax_h3_audio_vae_fp32.safetensors` | `vae` | https://huggingface.co/Comfy-Org/MiniMax-H3 |
| 高速化LoRA（強く推奨） | `minimax_h3_turbo_4step_ckpt600_ema_V4.safetensors` | `loras` | https://huggingface.co/Abiray/MiniMax-H3-Turbo-Lora-Pruned-ComfyUI |

- 各リポジトリ内のファイルの位置は変わることがあるので、ダウンロード前にリポジトリのファイル一覧で**正確なファイル名**を確認する。
- 高速化LoRAがあると 8 ステップで生成できる（無いと 20〜30 ステップ必要）。

**確認:** 手順7の `/genso/api/status` で必須モデルが全て `"present": true` になる。

---

## 6. アプリの環境を作る

```powershell
cd "<このリポジトリ>"
.\setup.bat
```

`setup.bat` は `py -V:3.12` で `.venv` を作り、窓用の `pywebview` だけを入れます（1〜2分）。最後に `pause` で止まるので、エージェントから実行する場合は Enter を送るか、中身と同じコマンドを直接実行してください。

```powershell
py -V:3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install pywebview
```

**確認:** `.\.venv\Scripts\python.exe -c "import webview"` がエラーなく終わる。

---

## 7. 初回起動（ここは利用者の操作が必要）

```powershell
.\run.bat
```

- 初回だけ「ComfyUI の場所」を選ぶ画面が出ます。**利用者に `main.py` があるフォルダを選んでもらってください**（フォルダ選択ダイアログのため、エージェントからは操作できません）。
- 選ぶと、アプリが次を自動で行います:
  - このリポジトリ直下に `config.json` を作成（ComfyUI の場所と起動フラグ。Git 管理外）
  - `<ComfyUIフォルダ>\custom_nodes\genso` → このリポジトリの `genso` へのジャンクションを作成
  - ComfyUI をアプリ専用フラグで起動（ログは `engine.log`）
- 2回目以降は `run.bat` だけで起動します。窓を閉じるとエンジンも止まります。

**確認（エンジン起動後、別のターミナルから）:**

```powershell
Invoke-RestMethod http://127.0.0.1:8188/genso/api/status | ConvertTo-Json -Depth 4
```

`models.required` の全項目が `"present": true` なら導入完了です。`false` のものは手順5のファイル名・配置先を見直してください。

---

## 8. 任意: 日本語プロンプトの翻訳

[Ollama](https://ollama.com/) を入れ、次のモデルを取得すると「日本語 → 英語」ボタンが使えます。無くても英語で書けば全機能が使えます。

```powershell
ollama pull huihui_ai/qwen3-abliterated:latest
```

---

## 9. トラブル時

| 症状 | 対処 |
|---|---|
| 起動しない・画面が出ない | `engine.log` の末尾を読む。`venv\Scripts\python.exe` が無い構成なら手順2へ |
| 8188番ポートを別の ComfyUI が使っている | アプリはそれに「外部起動」として接続する。推奨フラグで動かすには、その ComfyUI を閉じてから `run.bat` |
| **1ステップ目が20分近く進まない**（エラーは出ず、GPU使用率100%） | VRAM からあふれた分を Windows が黙って共有メモリへ逃がしている可能性が高い。NVIDIA コントロールパネル →「3D 設定の管理」→「**CUDA - システムメモリ フォールバック ポリシー**」を「**システムメモリ フォールバックを優先しない**」にし、アプリの「エンジン再起動」を押す |
| RTX 20 シリーズで生成が必ず落ちる | 手順4 |
| VRAM 8GB で極端に遅い | 尺は 107 フレームまで、解像度は `(幅÷16)×(高さ÷16)` が 1,620 以下（例: 864×480）に抑える。詳細は README |

---

## 10. エージェントからアプリを操作する場合

利用者の画面に入力内容が映る形で操作できます（人と AI が同じ画面を共有する設計）。

1. `POST /genso/api/form` に `{"values": {...生成設定...}}` を送る → 利用者の窓のフォームに反映される
2. `POST /genso/api/generate`（ヘッダー `X-GENSO-Source: api`）に同じ設定を送る → 生成開始
3. 進捗は利用者の窓の進捗カードに出る。完了は `GET /history/<prompt_id>`（ComfyUI 標準 API）で確認できる

生成設定のキー（`mode` / `prompt` / `width` / `height` / `length` / `steps` / `seed` / `first_frame` / `loras` など）は `genso/__init__.py` の `parse_settings()` が検証しています。画像は先に `POST /upload/image`（ComfyUI 標準）で入力フォルダへ送り、そのファイル名を `first_frame` に渡します。

---

## 関連

- 連続生成の追加パック **RENSO（連創型 幻想）** は別リポジトリです。導入手順はそちらの `AI_SETUP.md` を参照してください。

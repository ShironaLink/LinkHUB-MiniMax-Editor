"""Windows pywebview shell for LinkHUB GENSO."""

from __future__ import annotations

import html
import json
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# Software rendering was tried here to keep the window off the GPU, on the
# theory that its VRAM slowed generation down. Measurement later pinned the
# slowdowns on frame count and resolution instead, and the software path went
# blank after hours of uptime — so the window renders normally again.

try:
    import webview
except ImportError:
    raise SystemExit("pywebview がありません。setup.bat を実行してください。")

if getattr(sys, "frozen", False):
    # Packaged as an exe: everything the app needs sits next to the exe, not
    # inside the bundle. `genso` in particular must stay a real folder on disk
    # because ComfyUI loads it through a junction.
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.json"
LOG_PATH = ROOT / "engine.log"

# Two apps, one shell. They share an engine, a config file and a custom node;
# what differs is the page that is opened and the name on the window. Pick with
# `python wrapper\app.py renso` or GENSO_APP=renso.
VARIANTS: dict[str, dict[str, Any]] = {
    "genso": {
        "title": "LinkHUB　零式　MINI MAX簡易エディタ",
        "sub": "MINI MAX簡易エディタ",
        "route": "genso",
        "size": (1280, 750),
        "min_size": (980, 700),
    },
    "renso": {
        "title": "LinkHUB　零式　連創型　幻想",
        "sub": "連創型 幻想 · 連続生成",
        "route": "renso",
        # The rail is a horizontal row of cards, so the chain app opens wider
        # than the single-shot editor or the first two segments are off-screen.
        "size": (1680, 940),
        "min_size": (1120, 760),
    },
}


def _variant_name() -> str:
    requested = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("GENSO_APP", "")).strip().lower()
    return requested if requested in VARIANTS else "genso"


VARIANT_NAME = _variant_name()
VARIANT = VARIANTS[VARIANT_NAME]
WINDOW_TITLE = VARIANT["title"]

SPLASH = """<!doctype html><html lang=\"ja\"><meta charset=\"utf-8\">
<style>html,body{height:100%;margin:0;background:#000;color:#fff;font-family:'Yu Gothic UI',sans-serif}
body{display:grid;place-items:center}.box{text-align:center}.mark{font-size:40px;font-weight:900;letter-spacing:.08em}
.sub{margin-top:9px;color:#ffffff66;font-size:12px;letter-spacing:.22em}.wait{margin-top:38px;color:#ffffff99}
.line{width:220px;height:2px;background:#ffffff18;margin:16px auto;overflow:hidden}.line:after{content:'';display:block;width:45%;height:100%;background:#3FA9F5;animation:a 1.4s infinite ease-in-out}
@keyframes a{from{transform:translateX(-110%)}to{transform:translateX(330%)}}</style>
<body><div class=\"box\"><div class=\"mark\">LinkHUB　零式</div><div class=\"sub\">MINI MAX簡易エディタ</div>
<div class=\"wait\">エンジン起動中…（初回は15秒ほど）</div><div class=\"line\"></div></div></body></html>"""

# Both pages carry the app name in one place, so a single substitution is all
# the variant needs (a no-op for GENSO itself).
SPLASH = SPLASH.replace("MINI MAX簡易エディタ", VARIANT["sub"])


DEFAULT_CONFIG: dict[str, Any] = {
    "comfy_dir": "",
    "port": 8188,
    "output_subfolder": "GENSO",
    "translator": {
        "url": "http://127.0.0.1:11434",
        "model": "huihui_ai/qwen3-abliterated:latest",
        "timeout_sec": 300,
        "keep_alive": 0,
    },
    "profile": "win_8gb_turing",
    "profiles": {
        "win_8gb_turing": {
            "flags": [
                "--port", "8188",
                "--use-pytorch-cross-attention",
                "--preview-method", "none",
                "--reserve-vram", "0.6",
                "--fast", "fp16_accumulation",
                "--cache-none",
            ]
        }
    },
}


def _read_config() -> dict[str, Any]:
    """Load config.json, falling back to defaults on a fresh install.

    A downloaded copy ships without a ComfyUI path, so a missing or unreadable
    file is the normal first-run state rather than an error: the setup screen
    asks for the folder and writes it back here.
    """
    if not CONFIG_PATH.is_file():
        return dict(DEFAULT_CONFIG)
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as handle:
            stored = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)
    merged = dict(DEFAULT_CONFIG)
    merged.update(stored if isinstance(stored, dict) else {})
    return merged


def _write_comfy_dir(config: dict[str, Any], comfy_dir: Path) -> None:
    config["comfy_dir"] = str(comfy_dir)
    with CONFIG_PATH.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)


def check_comfy_dir(path: str) -> tuple[bool, str]:
    """Is this really a ComfyUI installation we can drive?

    Checked before writing anything, so a mistyped folder is rejected with a
    reason instead of failing later inside engine startup.
    """
    if not path.strip():
        return False, "フォルダが選ばれていません"
    root = Path(path)
    if not root.is_dir():
        return False, "そのフォルダが見つかりません"
    if not (root / "main.py").is_file():
        return False, "main.py がありません。ComfyUI 本体のフォルダを選んでください"
    if not (root / "custom_nodes").is_dir():
        return False, "custom_nodes フォルダがありません"
    if not (root / "venv" / "Scripts" / "python.exe").is_file():
        return False, "venv\\Scripts\\python.exe がありません。ComfyUI の Python 環境が必要です"
    return True, "ComfyUI を確認しました"


class EngineManager:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.comfy_dir = Path(config["comfy_dir"])
        self.port = int(config["port"])
        self.python = self.comfy_dir / "venv" / "Scripts" / "python.exe"
        self.link = self.comfy_dir / "custom_nodes" / "genso"
        self.process: subprocess.Popen[bytes] | None = None
        self.attached_owned_pid: int | None = None
        self.log_handle: Any = None
        self.external = False
        self._lock = threading.RLock()

    def rebind(self, comfy_dir: Path) -> None:
        """Point the manager at a folder chosen after startup."""
        with self._lock:
            self.comfy_dir = comfy_dir
            self.python = comfy_dir / "venv" / "Scripts" / "python.exe"
            self.link = comfy_dir / "custom_nodes" / "genso"

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def system_stats(self) -> dict[str, Any] | None:
        try:
            with urllib.request.urlopen(f"{self.base_url}/system_stats", timeout=3) as response:
                result = json.loads(response.read().decode("utf-8"))
            if result.get("system", {}).get("comfyui_version"):
                return result
        except Exception:
            return None
        return None

    def port_is_open(self) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=1):
                return True
        except OSError:
            return False

    def state(self) -> dict[str, Any]:
        owned = (self.process is not None and self.process.poll() is None) or (
            self.attached_owned_pid is not None and self.system_stats() is not None
        )
        return {
            "state": "ready" if self.system_stats() else ("starting" if owned else "stopped"),
            "owned": owned,
            "external": bool(self.external and not owned),
            "port": self.port,
        }

    def ensure_link(self) -> tuple[bool, str]:
        """Link this app's `genso` folder into ComfyUI's custom_nodes.

        A junction (mklink /J) needs no administrator rights and leaves the
        ComfyUI install untouched apart from that one entry, so the app can
        set itself up on first run instead of asking for a separate step.
        """
        if self.link.exists():
            return True, ""
        source = ROOT / "genso"
        if not source.is_dir():
            return False, f"genso フォルダが見つかりません: {source}"
        try:
            self.link.parent.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(self.link), str(source)],
                capture_output=True, text=True, timeout=20,
            )
        except Exception as exc:
            return False, f"custom_nodes へのリンクを作成できません: {exc}"
        if not self.link.exists():
            detail = (result.stderr or result.stdout or "").strip()[:200]
            return False, f"custom_nodes へのリンクを作成できません: {detail}"
        return True, ""

    def start(self) -> dict[str, Any]:
        with self._lock:
            linked, link_error = self.ensure_link()
            if not linked:
                return {"ok": False, "error": link_error}
            if self.system_stats():
                if os.environ.get("GENSO_ATTACH_OWNED") == "1":
                    self.attached_owned_pid = self._foreign_comfy_pid()
                    if self.attached_owned_pid is None:
                        return {"ok": False, "error": "実行中のGENSOエンジンを安全に引き継げませんでした"}
                    self.external = False
                    return {"ok": True, "external": False}
                self.external = self.process is None or self.process.poll() is not None
                return {"ok": True, "external": self.external}
            if self.port_is_open():
                return {"ok": False, "error": f"ポート {self.port} を別のアプリが使用しています"}
            if not self.python.is_file():
                return {"ok": False, "error": f"ComfyUI の Python が見つかりません: {self.python}"}
            profile = self.config["profile"]
            flags = list(self.config["profiles"][profile]["flags"])
            self.log_handle = LOG_PATH.open("a", encoding="utf-8")
            self.log_handle.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] GENSO engine start\n")
            self.log_handle.flush()
            creation_flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            self.process = subprocess.Popen(
                [str(self.python), "-u", "main.py", *flags],
                cwd=str(self.comfy_dir),
                stdout=self.log_handle,
                stderr=subprocess.STDOUT,
                creationflags=creation_flags,
            )
            self.external = False

        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if self.system_stats():
                return {"ok": True, "external": False}
            if self.process is None or self.process.poll() is not None:
                self._close_log()
                return {"ok": False, "error": "ComfyUI が起動直後に終了しました", "log": self.log_tail()}
            time.sleep(2)
        log = self.log_tail()
        self.stop(include_external=False)
        return {"ok": False, "error": "起動が120秒でタイムアウトしました", "log": log}

    def _close_log(self) -> None:
        if self.log_handle is not None:
            try:
                self.log_handle.close()
            except Exception:
                pass
            self.log_handle = None

    @staticmethod
    def _kill_tree(pid: int) -> None:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def _foreign_comfy_pid(self) -> int | None:
        script = (
            f"$c=Get-NetTCPConnection -LocalPort {self.port} -State Listen -ErrorAction SilentlyContinue | "
            "Select-Object -First 1; if($c){$proc=Get-CimInstance Win32_Process "
            "-Filter \"ProcessId=$($c.OwningProcess)\"; Write-Output \"$($proc.ProcessId)|$($proc.CommandLine)\"}"
        )
        try:
            output = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            ).stdout.strip()
        except Exception:
            return None
        pid, separator, command = output.partition("|")
        if not separator or "python" not in command.lower() or "main.py" not in command.lower():
            return None
        try:
            return int(pid)
        except ValueError:
            return None

    def stop(self, include_external: bool = False) -> dict[str, Any]:
        with self._lock:
            if self.process is not None and self.process.poll() is None:
                self._kill_tree(self.process.pid)
                self.process = None
                self._close_log()
                return {"ok": True}
            if self.attached_owned_pid is not None:
                self._kill_tree(self.attached_owned_pid)
                self.attached_owned_pid = None
                self._close_log()
                return {"ok": True}
            self.process = None
            self._close_log()
            if not include_external or not self.system_stats():
                return {"ok": True}
            pid = self._foreign_comfy_pid()
            if pid is None:
                return {"ok": False, "error": "外部 ComfyUI のプロセスを安全に特定できませんでした"}
            self._kill_tree(pid)
            for _ in range(15):
                if not self.system_stats():
                    self.external = False
                    return {"ok": True}
                time.sleep(1)
            return {"ok": False, "error": f"ComfyUI (PID {pid}) を停止できませんでした"}

    def restart(self) -> dict[str, Any]:
        stopped = self.stop(include_external=True)
        if not stopped.get("ok"):
            return stopped
        time.sleep(2)
        return self.start()

    def log_tail(self, line_count: int = 20) -> str:
        try:
            with LOG_PATH.open("r", encoding="utf-8", errors="replace") as handle:
                return "".join(handle.readlines()[-line_count:])
        except OSError:
            return ""


class WindowApi:
    def __init__(self, manager: EngineManager) -> None:
        self.manager = manager
        # pywebview recursively exposes public js_api attributes. Keeping its
        # own Window object in a public ``window`` attribute makes that scan
        # walk into WinForms/WebView2 COM properties from a worker thread,
        # which hangs the native window. Private attributes are not exported.
        self._window: Any = None
        # Private for the same reason: a public callable would be exported to
        # JS and re-scanned by pywebview on every call.
        self._boot: Any = None
        # One engine operation at a time. Two overlapping restarts would race
        # over the same port and the second would report a false failure.
        self._busy = False

    def engine_state(self) -> dict[str, Any]:
        return self.manager.state()

    # Both engine controls destroy the web server that serves the page they
    # were pressed on, so neither can deliver a return value to the browser:
    # the document is gone by the time the work finishes and pywebview's
    # evaluate_js of the pending callback raises JavascriptException on its
    # worker thread, taking the whole app down (observed 2026-09-22).
    #
    # Both therefore answer at once and hand the work to a thread. Python
    # outlives the page, so Python is what puts the window somewhere sensible
    # afterwards.

    def restart_engine(self) -> dict[str, Any]:
        if self._busy:
            return {"ok": True, "started": True, "note": "すでに処理中です"}
        self._busy = True
        threading.Thread(target=self._restart_worker, daemon=True).start()
        return {"ok": True, "started": True}

    def _restart_worker(self) -> None:
        try:
            result = self.manager.restart()
            window = self._window
            if window is None:
                return
            if result.get("ok"):
                window.load_url(f"{self.manager.base_url}/{VARIANT['route']}?shell=owned")
            else:
                window.load_html(
                    _error_page(result.get("error", "再起動に失敗しました"), result.get("log", ""))
                )
        except Exception:
            window = self._window
            if window is not None:
                window.load_html(_error_page("エンジンの再起動に失敗しました", traceback.format_exc()))
        finally:
            self._busy = False

    def stop_engine(self) -> dict[str, Any]:
        """Shut the engine down but leave this window open.

        Generation and any other GPU work compete for the same VRAM, so
        upscaling or a second tool runs far faster with the engine unloaded.
        `include_external=True` so an engine the user started outside GENSO is
        stopped too — otherwise the button would appear to do nothing.
        """
        if self._busy:
            return {"ok": True, "started": True, "note": "すでに処理中です"}
        self._busy = True
        threading.Thread(target=self._stop_worker, daemon=True).start()
        return {"ok": True, "started": True}

    def _stop_worker(self) -> None:
        try:
            result = self.manager.stop(include_external=True)
            window = self._window
            if window is None:
                return
            if result.get("ok"):
                # The app page cannot be reloaded with the server gone, so the
                # window gets a page of its own that can start the engine again.
                window.load_html(STOPPED_PAGE)
            else:
                window.load_html(_error_page(result.get("error", "停止に失敗しました")))
        except Exception:
            window = self._window
            if window is not None:
                window.load_html(_error_page("エンジンの停止に失敗しました", traceback.format_exc()))
        finally:
            self._busy = False

    def pick_file(self, kind: str = "all") -> dict[str, Any]:
        if self._window is None:
            return {"ok": False, "error": "ウィンドウの準備ができていません"}
        filters = {
            "image": ("画像 (*.png;*.jpg;*.jpeg;*.webp;*.bmp)",),
            "video": ("動画 (*.mp4;*.mov;*.mkv;*.webm)",),
            "audio": ("音声 (*.wav;*.mp3;*.m4a;*.flac;*.ogg)",),
        }.get(kind, ("すべてのファイル (*.*)",))
        paths = self._window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False, file_types=filters)
        return {"ok": True, "path": paths[0] if paths else None}

    def pick_comfy_dir(self) -> dict[str, Any]:
        """Ask for the ComfyUI folder, validate it, and remember it."""
        if self._window is None:
            return {"ok": False, "error": "ウィンドウの準備ができていません"}
        picked = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        if not picked:
            return {"ok": False, "error": "選択がキャンセルされました"}
        chosen = picked[0] if isinstance(picked, (list, tuple)) else str(picked)
        valid, message = check_comfy_dir(chosen)
        if not valid:
            return {"ok": False, "error": message, "path": chosen}
        return {"ok": True, "path": chosen, "message": message}

    def save_comfy_dir(self, path: str) -> dict[str, Any]:
        """Persist the chosen folder and boot the engine from it."""
        valid, message = check_comfy_dir(path)
        if not valid:
            return {"ok": False, "error": message}
        try:
            _write_comfy_dir(self.manager.config, Path(path))
        except OSError as exc:
            return {"ok": False, "error": f"config.json を保存できません: {exc}"}
        self.manager.rebind(Path(path))
        if self._boot is None:
            return {"ok": False, "error": "起動処理が準備できていません"}
        # Boot off the UI thread so the setup page can keep responding while
        # ComfyUI starts; boot() swaps the page itself when it is ready.
        threading.Thread(target=self._boot, daemon=True).start()
        return {"ok": True}


SETUP_PAGE = """<!doctype html><html lang="ja"><meta charset="utf-8"><style>
*{box-sizing:border-box}html,body{height:100%;margin:0;background:#000;color:#fff;
font-family:'Yu Gothic UI',sans-serif}
body{display:grid;place-items:center;padding:40px}
.card{width:100%;max-width:720px;border:1px solid #ffffff1a;border-radius:18px;
padding:38px 40px;background:#ffffff08}
.mark{font-size:30px;font-weight:900;letter-spacing:.06em}
.sub{margin-top:8px;color:#ffffff66;font-size:12px;letter-spacing:.22em}
h1{font-size:17px;font-weight:700;margin:34px 0 10px}
p{color:#ffffffb3;font-size:13px;line-height:1.85;margin:0 0 8px}
.path{margin:18px 0 6px;padding:13px 15px;border:1px solid #ffffff1a;border-radius:10px;
background:#00000066;color:#ffffff80;font-size:12px;word-break:break-all;min-height:44px}
.path.set{color:#fff;border-color:#3FA9F566}
.row{display:flex;gap:12px;margin-top:22px}
button{font:inherit;font-size:14px;padding:12px 22px;border-radius:10px;cursor:pointer;
border:1px solid #ffffff26;background:#ffffff0d;color:#fff;transition:.15s}
button:hover:not(:disabled){background:#ffffff1a}
button.go{background:#3FA9F5;border-color:#3FA9F5;color:#001523;font-weight:700}
button.go:hover:not(:disabled){background:#5fb8f7}
button:disabled{opacity:.35;cursor:default}
.msg{margin-top:16px;font-size:13px;min-height:20px}
.msg.err{color:#fca5a5}.msg.ok{color:#7dd3a0}
</style><body><div class="card">
<div class="mark">LinkHUB　零式</div><div class="sub">MINI MAX簡易エディタ</div>
<h1>最初に ComfyUI の場所を教えてください</h1>
<p>このアプリは ComfyUI をエンジンとして使います。ComfyUI 本体が入っているフォルダ
（<code>main.py</code> があるフォルダ）を選んでください。</p>
<p>Stability Matrix をお使いの場合は <code>Packages\\ComfyUI</code> がその場所です。</p>
<div class="path" id="path">まだ選ばれていません</div>
<div class="row">
  <button id="pick">フォルダを選ぶ</button>
  <button id="go" class="go" disabled>この場所で起動</button>
</div>
<div class="msg" id="msg"></div>
</div><script>
const $ = (id) => document.getElementById(id);
let chosen = null;
$("pick").addEventListener("click", async () => {
  $("msg").className = "msg"; $("msg").textContent = "";
  const r = await window.pywebview.api.pick_comfy_dir();
  if (r.ok) {
    chosen = r.path;
    $("path").textContent = r.path;
    $("path").classList.add("set");
    $("go").disabled = false;
    $("msg").className = "msg ok"; $("msg").textContent = r.message;
  } else {
    $("go").disabled = true;
    if (r.path) { $("path").textContent = r.path; }
    $("msg").className = "msg err"; $("msg").textContent = r.error;
  }
});
$("go").addEventListener("click", async () => {
  if (!chosen) return;
  $("go").disabled = true; $("pick").disabled = true;
  $("msg").className = "msg"; $("msg").textContent = "エンジンを起動しています…";
  const r = await window.pywebview.api.save_comfy_dir(chosen);
  if (!r.ok) {
    $("go").disabled = false; $("pick").disabled = false;
    $("msg").className = "msg err"; $("msg").textContent = r.error;
  }
});
</script></body></html>"""

SETUP_PAGE = SETUP_PAGE.replace("MINI MAX簡易エディタ", VARIANT["sub"])


STOPPED_PAGE = """<!doctype html><html lang=\"ja\"><meta charset=\"utf-8\"><style>
html,body{height:100%;margin:0;background:#000;color:#fff;font-family:'Yu Gothic UI',sans-serif}
body{display:grid;place-items:center}.box{text-align:center}
.mark{font-size:28px;font-weight:900;letter-spacing:.06em}
.sub{margin-top:10px;color:#ffffff66;font-size:12px;letter-spacing:.2em}
p{margin:26px 0 0;color:#ffffffb3;font-size:13px;line-height:1.9}
button{margin-top:26px;font:inherit;font-size:14px;padding:12px 26px;border-radius:10px;
cursor:pointer;border:1px solid #3FA9F5;background:#3FA9F5;color:#001523;font-weight:700}
button:disabled{opacity:.4;cursor:default}
</style><body><div class=\"box\"><div class=\"mark\">エンジンを停止しました</div>
<div class=\"sub\">VRAM は解放されています</div>
<p>他の生成ツールを使い終わったら、ここから再開できます。<br>
生成した動画とプランは消えていません。</p>
<button id=\"go\" type=\"button\">エンジンを起動して戻る</button></div><script>
document.getElementById("go").addEventListener("click", async (event) => {
  event.target.disabled = true;
  event.target.textContent = "起動中…（初回は15秒ほど）";
  await window.pywebview.api.restart_engine();
});
</script></body></html>"""


def _error_page(message: str, log: str = "") -> str:
    return f"""<!doctype html><html lang=\"ja\"><meta charset=\"utf-8\"><style>
body{{background:#000;color:#fff;font:14px 'Yu Gothic UI',sans-serif;padding:50px}}
.card{{max-width:850px;margin:auto;border:1px solid #ffffff20;border-radius:16px;padding:28px;background:#ffffff0d}}
h1{{font-size:22px}}p{{color:#fcd34d}}pre{{white-space:pre-wrap;color:#ffffff99;background:#080808;padding:16px;border-radius:8px}}
</style><body><div class=\"card\"><h1>{html.escape(WINDOW_TITLE)}を起動できませんでした</h1><p>{html.escape(message)}</p>
<pre>{html.escape(log)}</pre></div></body></html>"""


def main() -> None:
    config = _read_config()
    configured = check_comfy_dir(str(config.get("comfy_dir", "")))[0]

    manager = EngineManager(config)
    api = WindowApi(manager)
    window = webview.create_window(
        WINDOW_TITLE,
        html=SPLASH if configured else SETUP_PAGE,
        width=VARIANT["size"][0],
        height=VARIANT["size"][1],
        min_size=VARIANT["min_size"],
        js_api=api,
        background_color="#000000",
    )
    api._window = window

    def boot() -> None:
        try:
            window.load_html(SPLASH)
            result = manager.start()
            if result.get("ok"):
                attached_owned = os.environ.get("GENSO_ATTACH_OWNED") == "1"
                shell_mode = "owned" if attached_owned else ("external" if result.get("external") else "owned")
                # Pass the ownership state in the URL. Calling the Python JS API
                # automatically while WebView2 is still finishing navigation can
                # deadlock pywebview 6.2.1 on WinForms.
                window.load_url(f"{manager.base_url}/{VARIANT['route']}?shell={shell_mode}")
            else:
                window.load_html(_error_page(result.get("error", "不明なエラー"), result.get("log", "")))
        except Exception:
            detail = traceback.format_exc()
            try:
                with LOG_PATH.open("a", encoding="utf-8") as handle:
                    handle.write("\n[GENSO wrapper bootstrap error]\n" + detail)
            finally:
                window.load_html(_error_page("GENSO の画面初期化に失敗しました", detail))

    api._boot = boot

    def bootstrap() -> None:
        # With no ComfyUI folder saved yet the setup page is already showing;
        # it calls save_comfy_dir(), which boots the engine once a valid
        # folder is picked.
        if configured:
            boot()

    def closed() -> None:
        manager.stop(include_external=False)

    window.events.closed += closed
    webview.start(bootstrap, gui="edgechromium", debug=False)


if __name__ == "__main__":
    main()

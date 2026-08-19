"""Server-authoritative UI session state, so people and agents share one screen.

The original design kept "which job is running" in a browser variable, which meant
a job was only visible to the tab that submitted it — an agent calling the HTTP API
left the window blank, a second window saw nothing, and a reload lost the job.

Here the engine owns two pieces of state and pushes them over the WebSocket that
ComfyUI already maintains with every client:

  job   the generation currently in flight, whoever started it
  form  the editor's field values, so an agent can fill the form in and a human
        can look it over and press the button

Both are plain dicts; the transport is `PromptServer.send_sync`, whose messages
arrive at the browser as {"type": <event>, "data": <payload>}.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Awaitable

JOB_EVENT = "genso.job"
FORM_EVENT = "genso.form"

_job: dict[str, Any] | None = None
_form: dict[str, Any] | None = None


def _broadcast(event: str, data: dict[str, Any] | None) -> None:
    """Push to every connected client. Never let a dead socket break a request."""
    try:
        from server import PromptServer

        PromptServer.instance.send_sync(event, {"payload": data})
    except Exception:
        pass


def snapshot() -> dict[str, Any]:
    return {"job": _job, "form": _form}


def get_job() -> dict[str, Any] | None:
    return _job


def start_job(prompt_id: str, settings: dict[str, Any], source: str) -> dict[str, Any]:
    """Record the job and tell every client, regardless of who submitted it.

    `source` is "ui" or "api" purely so the screen can say where it came from;
    both paths are otherwise identical.
    """
    global _job
    _job = {
        "prompt_id": prompt_id,
        "settings": settings,
        "source": source,
        "started_at": time.time(),
        "steps": int(settings.get("steps") or 0),
    }
    set_form(settings, source=source, broadcast=False)
    _broadcast(JOB_EVENT, _job)
    return _job


def finish_job(prompt_id: str | None = None, status: str = "done") -> None:
    """Clear the job. A mismatched prompt_id is ignored so a late report from an
    old job cannot wipe the one that replaced it."""
    global _job
    if _job is None:
        return
    if prompt_id is not None and _job.get("prompt_id") != prompt_id:
        return
    _job = None
    _broadcast(JOB_EVENT, None)


def set_form(values: dict[str, Any], source: str = "api", broadcast: bool = True) -> dict[str, Any]:
    global _form
    _form = {"values": values, "source": source, "updated_at": time.time()}
    if broadcast:
        _broadcast(FORM_EVENT, _form)
    return _form


async def reconcile(
    comfy_request: Callable[..., Awaitable[tuple[int, Any]]],
) -> dict[str, Any] | None:
    """Drop a job the engine is no longer running.

    Without this, a job that ended while no window was open would stay on screen
    forever — and worse, would keep the generate button locked. Asking the engine
    is the only trustworthy source; a client's word is not enough because the
    client may have been closed mid-run.
    """
    if _job is None:
        return None
    prompt_id = _job.get("prompt_id")
    try:
        status, queue = await comfy_request("GET", "/queue")
        if status != 200:
            return _job
        for key in ("queue_running", "queue_pending"):
            for entry in queue.get(key) or []:
                if len(entry) > 1 and entry[1] == prompt_id:
                    return _job
        history_status, history = await comfy_request("GET", f"/history/{prompt_id}")
        if history_status == 200 and prompt_id in (history or {}):
            finish_job(prompt_id)
            return None
        # Not queued and not in history: the engine was restarted under us.
        finish_job(prompt_id)
        return None
    except Exception:
        return _job

"""Headless Claude Code wrapper.

Runs `claude -p` as a subprocess and returns validated JSON. This is the only
place the pipeline talks to a model, so retry, extraction and schema checks all
live here rather than being duplicated per format.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any, Callable

CLAUDE_BIN = shutil.which("claude") or "claude"
DEFAULT_TIMEOUT = 300

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class LLMError(RuntimeError):
    pass


def _extract_json(text: str) -> Any:
    """Pull a JSON object out of model output, fenced or bare."""
    candidates: list[str] = []
    for m in _FENCE.finditer(text):
        candidates.append(m.group(1))
    candidates.append(text)

    # Also try the outermost {...} / [...] span as a last resort.
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            candidates.append(text[start : end + 1])

    for blob in candidates:
        blob = blob.strip()
        if not blob:
            continue
        try:
            return json.loads(blob)
        except json.JSONDecodeError:
            continue
    raise LLMError(f"no parseable JSON in model output:\n{text[:800]}")


SYSTEM_PROMPT = (
    "You are a JSON generation service. You never ask clarifying questions and "
    "never explain yourself. You emit exactly one JSON value and nothing else."
)


def _run_claude(prompt: str, timeout: int) -> str:
    # The prompt goes on stdin, not argv: on Windows `claude` resolves to a .cmd
    # shim, and cmd.exe truncates a multi-line argument at the first newline.
    #
    # Empty `hooks` stops the calling session's UserPromptSubmit hooks from
    # injecting a writing style into generated narration. (--bare would also do
    # this, but it skips keychain reads and so fails with "Not logged in".)
    proc = subprocess.run(
        [
            CLAUDE_BIN,
            "-p",
            "--settings", '{"hooks":{}}',
            "--output-format", "json",
            "--system-prompt", SYSTEM_PROMPT,
        ],
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise LLMError(f"claude exited {proc.returncode}: {proc.stderr.strip()[:500]}")

    # --output-format json wraps the reply in an envelope with a `result` field.
    try:
        envelope = json.loads(proc.stdout)
        if isinstance(envelope, dict) and "result" in envelope:
            return str(envelope["result"])
    except json.JSONDecodeError:
        pass
    return proc.stdout


def ask_json(
    prompt: str,
    *,
    validate: Callable[[Any], None] | None = None,
    attempts: int = 3,
    timeout: int = DEFAULT_TIMEOUT,
) -> Any:
    """Ask Claude for JSON, retrying with the error fed back on failure."""
    last_err: Exception | None = None
    current = prompt

    for attempt in range(1, attempts + 1):
        try:
            raw = _run_claude(current, timeout)
            data = _extract_json(raw)
            if validate:
                validate(data)
            return data
        except (LLMError, ValueError, KeyError, TypeError) as exc:
            last_err = exc
            if attempt == attempts:
                break
            current = (
                f"{prompt}\n\n---\nYour previous reply was rejected: {exc}\n"
                "Return ONLY valid JSON matching the schema. No prose, no code fence."
            )

    raise LLMError(f"failed after {attempts} attempts: {last_err}")

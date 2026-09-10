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
import time
from typing import Any, Callable

DEFAULT_TIMEOUT = 300

# Claude Code updates itself with `npm install --global`, which deletes and
# rewrites the shim and its 200 MB exe. For those seconds the binary is not
# there, and an unattended run that happens to start inside that window loses
# every video it was going to make. So: wait the swap out instead of failing.
BIN_WAIT = 180          # how long a single call waits for the binary to reappear
BIN_POLL = 5            # how often it looks while waiting
SWAP_BACKOFF = 20       # pause before retrying a call the swap already broke
MAX_SWAP_WAIT = 120     # total time ask_json will spend on those retries

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

# A shim that exists but points at a half-written exe fails in cmd.exe, not in
# CreateProcess, so it arrives as a non-zero exit rather than FileNotFoundError.
_MISSING_BIN = re.compile(
    r"cannot find the (?:file|path) specified"
    r"|is not recognized as an internal or external command",
    re.IGNORECASE,
)


class LLMError(RuntimeError):
    pass


class ClaudeUnavailable(LLMError):
    """The `claude` binary could not be found or executed at all.

    Distinct from a bad answer: the model never saw the prompt, so there is
    nothing to feed back and retrying costs nothing but time.
    """


def wait_for_claude(timeout: int = BIN_WAIT) -> str:
    """Return the path to `claude`, waiting up to `timeout` for it to exist.

    Resolved per call rather than once at import: a path looked up when the
    process started can be stale by the time a run reaches the model, which is
    exactly what happens when the updater fires at login alongside the daily
    task.
    """
    deadline = time.monotonic() + timeout
    while True:
        found = shutil.which("claude")
        if found:
            return found
        if time.monotonic() >= deadline:
            raise ClaudeUnavailable(
                f"`claude` is not on PATH after waiting {timeout}s. Either a "
                "self-update is stuck, or it is not installed: "
                "npm install --global @anthropic-ai/claude-code"
            )
        time.sleep(BIN_POLL)


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
    try:
        proc = subprocess.run(
            [
                wait_for_claude(),
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
    except OSError as exc:
        # FileNotFoundError included: the shim was there when it was resolved
        # and gone by the time CreateProcess ran.
        raise ClaudeUnavailable(f"could not execute `claude`: {exc}") from exc

    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        if _MISSING_BIN.search(stderr):
            raise ClaudeUnavailable(f"`claude` is mid-update: {stderr[:200]}")
        raise LLMError(f"claude exited {proc.returncode}: {stderr[:500]}")

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
    attempt = 0
    swap_waited = 0

    while attempt < attempts:
        try:
            raw = _run_claude(current, timeout)
            data = _extract_json(raw)
            if validate:
                validate(data)
            return data
        except ClaudeUnavailable as exc:
            # Not a failed attempt - the model never got the prompt, so there
            # is nothing to correct and nothing to charge to the retry budget.
            # Wait for the updater to put the binary back and try the same
            # prompt again.
            last_err = exc
            if swap_waited >= MAX_SWAP_WAIT:
                break
            time.sleep(SWAP_BACKOFF)
            swap_waited += SWAP_BACKOFF
        except (LLMError, ValueError, KeyError, TypeError) as exc:
            attempt += 1
            last_err = exc
            if attempt >= attempts:
                break
            current = (
                f"{prompt}\n\n---\nYour previous reply was rejected: {exc}\n"
                "Return ONLY valid JSON matching the schema. No prose, no code fence."
            )

    if isinstance(last_err, ClaudeUnavailable):
        raise ClaudeUnavailable(
            f"`claude` never became usable ({swap_waited}s of retries): {last_err}")
    raise LLMError(f"failed after {attempts} attempts: {last_err}")

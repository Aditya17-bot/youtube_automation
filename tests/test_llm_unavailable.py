"""A missing `claude` binary must cost a wait, not the day.

Claude Code updates itself with a global npm install, which deletes and
rewrites its own shim. Task Scheduler starts the daily run at login when the
laptop was asleep at 09:00, and the updater runs at login too - on 2026-09-10
they landed 49 seconds apart and every video that day died on WinError 2 with
no retry, because FileNotFoundError was not in the retryable set.

These tests pin the three things that fixes it: the binary is resolved per
call, a vanished binary is retried rather than raised, and the retries it
spends do not eat the budget meant for bad model answers.
"""
import sys, pathlib, time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from core import llm

real_which, real_sleep, real_run = llm.shutil.which, llm.time.sleep, llm._run_claude
slept: list[float] = []
llm.time.sleep = slept.append          # no test waits three real minutes

try:
    # --- the binary is looked up per call, not once at import ------------
    llm.shutil.which = lambda name: None
    try:
        llm.wait_for_claude(timeout=0)
        raise AssertionError("a missing binary must raise")
    except llm.ClaudeUnavailable as exc:
        assert "not on PATH" in str(exc), exc
    print("missing binary raises ClaudeUnavailable, not FileNotFoundError")

    llm.shutil.which = lambda name: r"C:\fake\claude.CMD"
    assert llm.wait_for_claude(timeout=0) == r"C:\fake\claude.CMD"
    print("the path is resolved at call time, so a reinstalled shim is picked up")

    # A shim that reappears part way through a wait is waited for, not failed.
    calls = {"n": 0}
    def late(name):
        calls["n"] += 1
        return r"C:\fake\claude.CMD" if calls["n"] > 3 else None
    llm.shutil.which = late
    assert llm.wait_for_claude(timeout=999) == r"C:\fake\claude.CMD"
    assert calls["n"] == 4, calls
    print(f"a swap in progress is polled {calls['n']} times, then proceeds")

    # --- ClaudeUnavailable is retried, and does not spend the budget -----
    llm.shutil.which = lambda name: r"C:\fake\claude.CMD"
    tries = {"n": 0}

    def gone(prompt, timeout):
        tries["n"] += 1
        raise llm.ClaudeUnavailable("`claude` is mid-update")

    llm._run_claude = gone
    slept.clear()
    try:
        llm.ask_json("give me json", attempts=3)
        raise AssertionError("it must eventually give up")
    except llm.ClaudeUnavailable as exc:
        assert "never became usable" in str(exc), exc

    expected = llm.MAX_SWAP_WAIT // llm.SWAP_BACKOFF
    assert tries["n"] == expected + 1, f"{tries['n']} calls, expected {expected + 1}"
    assert slept == [llm.SWAP_BACKOFF] * expected, slept
    assert sum(slept) == llm.MAX_SWAP_WAIT, "the waiting has to stay bounded"
    print(f"a swap is retried {expected} times over {llm.MAX_SWAP_WAIT}s, "
          "not once per attempt")

    # The whole point: a binary that comes back mid-run finishes the video,
    # and the prompt is re-sent unchanged - there is no reply to correct.
    seen: list[str] = []
    state = {"n": 0}

    def flaky(prompt, timeout):
        seen.append(prompt)
        state["n"] += 1
        if state["n"] < 3:
            raise llm.ClaudeUnavailable("`claude` is mid-update")
        return '{"ok": true}'

    llm._run_claude = flaky
    assert llm.ask_json("give me json", attempts=3) == {"ok": True}
    assert seen == ["give me json"] * 3, seen
    print("a binary that returns mid-run completes the call on the same prompt")

    # --- a bad answer still costs an attempt, and still gets corrected ---
    prompts: list[str] = []

    def junk(prompt, timeout):
        prompts.append(prompt)
        return "sorry, I cannot do that"

    llm._run_claude = junk
    try:
        llm.ask_json("give me json", attempts=3)
        raise AssertionError("unparseable output must still fail")
    except llm.ClaudeUnavailable as exc:      # noqa: F841 - wrong branch
        raise AssertionError("a bad answer is not an unavailable binary")
    except llm.LLMError as exc:
        assert "failed after 3 attempts" in str(exc), exc
    assert len(prompts) == 3, prompts
    assert "was rejected" in prompts[1], "the error must be fed back to the model"
    print("a bad answer still burns an attempt and is fed back for correction")

    print("\nALL LLM AVAILABILITY TESTS PASS")
finally:
    llm.shutil.which, llm.time.sleep, llm._run_claude = real_which, real_sleep, real_run

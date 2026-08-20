"""Email when an unattended run needs a human.

The daily task runs at 09:00 whether or not anyone is watching, and every
failure mode it has is silent. A render dies and the channel simply has no
video that day. A refresh token is revoked and uploads stop. A topic bank runs
dry and build_plan prints `skip finance_short` into a log file nobody opens.
None of that surfaces anywhere until you notice a channel has gone quiet, which
takes about a week.

Credentials live in `secrets/smtp.json`, which is gitignored along with the rest
of that directory. With no config file this module is a no-op that says so: an
alerting channel that cannot reach anyone is worth strictly less than a run that
finishes, so nothing here is ever allowed to raise into the caller.

    python -m core.notify --test
"""
from __future__ import annotations

import json
import smtplib
import socket
from email.message import EmailMessage
from pathlib import Path

from core.config import PATHS

CONFIG = PATHS.root / "secrets" / "smtp.json"

# Long enough that a bank refilled on the next weekend still never runs dry,
# short enough that the warning does not become background noise.
LOW_BANK_WEEKS = 2

TEMPLATE = {
    "host": "smtp.gmail.com",
    "port": 587,
    "user": "you@gmail.com",
    "password": "16-character Google app password, spaces are fine",
    "to": "you@gmail.com",
}


def configured() -> bool:
    return CONFIG.exists()


def _load() -> dict | None:
    if not CONFIG.exists():
        return None
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"notify: cannot read {CONFIG.name}: {exc}")
        return None
    missing = [k for k in ("host", "user", "password", "to") if not cfg.get(k)]
    if missing:
        print(f"notify: {CONFIG.name} is missing {', '.join(missing)}")
        return None
    return cfg


def send(subject: str, body: str) -> bool:
    """Send one mail. Returns whether it went. Never raises.

    A broken mailbox must not turn a run that produced four videos into a
    failed run, so every error here is reported to stdout and swallowed.
    """
    cfg = _load()
    if cfg is None:
        why = "unusable" if CONFIG.exists() else "missing"
        print(f"notify: {CONFIG.name} {why}, would have sent: {subject}")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["user"]
    msg["To"] = cfg["to"]
    msg.set_content(body)

    port = int(cfg.get("port", 587))
    try:
        # Timeouts are not optional here. Without one a hung SMTP connection
        # holds the task open until Scheduler kills it at the four-hour limit.
        if port == 465:
            server = smtplib.SMTP_SSL(cfg["host"], port, timeout=30)
        else:
            server = smtplib.SMTP(cfg["host"], port, timeout=30)
        with server:
            if port != 465:
                server.starttls()
            # Gmail rejects the account password outright; this must be an app
            # password from a 2FA-enabled account.
            server.login(cfg["user"], cfg["password"].replace(" ", ""))
            server.send_message(msg)
    except (smtplib.SMTPException, OSError, socket.timeout) as exc:
        print(f"notify: send failed: {type(exc).__name__}: {exc}")
        return False
    print(f"notify: emailed {cfg['to']}")
    return True


def low_banks(weeks: int = LOW_BANK_WEEKS) -> list[str]:
    """Channels about to run out of topics, worded as a deadline.

    Reported before it bites rather than after: an exhausted bank does not
    crash anything, it just quietly stops producing, which is the failure that
    takes longest to notice.
    """
    from core import ideate, schedule

    out = []
    for channel in schedule.all_channels():
        per_week = schedule.cadence(channel)
        if per_week <= 0:
            continue  # a channel that never runs cannot run dry
        try:
            left = ideate.stats(channel)["remaining"]
        except Exception:  # noqa: BLE001
            continue
        if left <= per_week * weeks:
            got = left / per_week
            out.append(f"{channel}: {left} topics left, about {got:.1f} weeks "
                       f"at {per_week}/week")
    return out


def log_tail(path: Path, lines: int = 40) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace")
                         .splitlines()[-lines:])
    except OSError as exc:
        return f"(could not read {path}: {exc})"


def report(day: str, problems: list[str], log: Path | None = None) -> bool:
    """Mail one digest for a run that had something wrong with it."""
    if not problems:
        return False
    subject = f"youtube_auto {day}: {len(problems)} problem(s)"
    body = [f"Run for {day} on {socket.gethostname()} reported:", ""]
    body += [f"  - {p}" for p in problems]
    if log is not None and log.exists():
        body += ["", f"Last lines of {log}:", "", log_tail(log)]
    return send(subject, "\n".join(body))


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="email alerting")
    ap.add_argument("--test", action="store_true", help="send a test message")
    ap.add_argument("--init", action="store_true", help="write a config template")
    args = ap.parse_args()

    if args.init:
        if CONFIG.exists():
            raise SystemExit(f"{CONFIG} already exists, not overwriting")
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps(TEMPLATE, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {CONFIG} - fill it in, then run --test")
    elif args.test:
        ok = send("youtube_auto test", "Alerting works. Nothing is wrong.")
        raise SystemExit(0 if ok else 1)
    else:
        print(f"config: {CONFIG} ({'present' if configured() else 'missing'})")
        for line in low_banks() or ["topic banks: all fine"]:
            print(f"  {line}")

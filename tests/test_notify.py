"""Alerting must never be the thing that breaks a run.

It exists to report failures, so a failure inside it has to stay contained:
a mailbox that is misconfigured, unreachable or slow must cost the run nothing.
The other half is the warning nobody would otherwise get - a topic bank going
quiet does not raise, it just stops producing videos.
"""
import sys, pathlib, json, shutil, smtplib, yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from core import notify
from core.config import PATHS

CH = "_test_notify"
cfg_path = PATHS.channels / f"{CH}.yaml"
bank_path = PATHS.channels / f"topics_{CH}.yaml"
tmp_cfg = PATHS.root / "secrets" / "_test_smtp.json"
real_smtp = smtplib.SMTP
real_config = notify.CONFIG

try:
    notify.CONFIG = tmp_cfg
    tmp_cfg.parent.mkdir(parents=True, exist_ok=True)
    if tmp_cfg.exists(): tmp_cfg.unlink()

    # --- no config ----------------------------------------------------------
    assert notify.configured() is False
    assert notify.send("subject", "body") is False, "no config must not claim success"
    print("no config: send declines quietly instead of raising")

    # --- half-written config -------------------------------------------------
    tmp_cfg.write_text(json.dumps({"host": "smtp.example.com", "user": "a@b.c"}),
                       encoding="utf-8")
    assert notify.send("s", "b") is False, "a config missing a password must not send"
    tmp_cfg.write_text("{not json", encoding="utf-8")
    assert notify.send("s", "b") is False, "unparseable config must not raise"
    print("broken config: refuses, reports why, does not raise")

    # --- the server is down --------------------------------------------------
    tmp_cfg.write_text(json.dumps({
        "host": "smtp.example.com", "port": 587,
        "user": "a@b.c", "password": "x", "to": "a@b.c"}), encoding="utf-8")

    class Boom:
        def __init__(self, *a, **k):
            raise OSError("connection refused")

    smtplib.SMTP = Boom
    assert notify.send("s", "b") is False, "an unreachable server must be swallowed"
    print("unreachable server: contained, run survives")

    # --- a send that works ---------------------------------------------------
    sent = {}

    class Fake:
        def __init__(self, host, port, timeout=None):
            sent["host"], sent["port"], sent["timeout"] = host, port, timeout
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): sent["tls"] = True
        def login(self, user, password): sent["password"] = password
        def send_message(self, msg):
            sent["subject"] = msg["Subject"]
            sent["body"] = msg.get_content()

    smtplib.SMTP = Fake
    tmp_cfg.write_text(json.dumps({
        "host": "smtp.gmail.com", "port": 587, "user": "a@b.c",
        "password": "abcd efgh ijkl mnop", "to": "d@e.f"}), encoding="utf-8")
    assert notify.send("hello", "world") is True
    assert sent["tls"] is True, "STARTTLS is not optional on port 587"
    assert sent["timeout"], "a hung connection would hold the task open for hours"
    assert sent["password"] == "abcdefghijklmnop", "app password spaces must be stripped"
    print("working send: STARTTLS on, timeout set, app password normalised")

    # --- report only mails when there is something to say --------------------
    sent.clear()
    assert notify.report("2026-08-21", []) is False, "a clean run must not send mail"
    assert not sent, "nothing should have been sent"
    assert notify.report("2026-08-21", ["finance: render failed"]) is True
    assert "2026-08-21" in sent["subject"]
    assert "finance: render failed" in sent["body"]
    print("report: silent on success, specific on failure")

    # --- low banks -----------------------------------------------------------
    cfg_path.write_text(yaml.safe_dump({
        "name": "test", "format": "finance",
        "voice": {"provider": "edge-tts", "name": "x"},
        "video": {"width": 1920, "height": 1080},
        "cadence": {"per_week": 5},
        "script": {"topics_from": CH},
    }), encoding="utf-8")
    bank_path.write_text(yaml.safe_dump({"topics": [
        {"id": "a", "title": "A"}, {"id": "b", "title": "B"},
    ]}), encoding="utf-8")

    warnings = notify.low_banks()
    mine = [w for w in warnings if w.startswith(CH)]
    assert mine, f"2 topics at 5/week should warn, got {warnings}"
    assert "2 topics left" in mine[0], mine[0]
    print(f"low bank warns: {mine[0]}")

    assert not any(w.startswith("product") for w in warnings), \
        "a channel that never runs cannot run dry, and must not nag"
    print("a zero-cadence channel is not reported as low")

    print("\nALL NOTIFY TESTS PASS")
finally:
    smtplib.SMTP = real_smtp
    notify.CONFIG = real_config
    for p in (cfg_path, bank_path, tmp_cfg):
        if p.exists(): p.unlink()
    for d in (PATHS.work / CH, PATHS.review / CH):
        if d.exists(): shutil.rmtree(d, ignore_errors=True)

#!/usr/bin/env python3
"""Standalone defaults: registry resolution and the optional notify hook."""

from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace

from .conftest import CLI, read_reg, subprocess_env


def _args(**kw):
    return SimpleNamespace(**{"registry": None, "events": None, **kw})


def test_registry_flag_wins_over_env(lp, tmp_path, monkeypatch):
    monkeypatch.setenv("LEAN_PKG_REGISTRY", str(tmp_path / "env.yaml"))
    assert lp.registry_path(_args(registry=str(tmp_path / "flag.yaml"))) \
        == tmp_path / "flag.yaml"


def test_registry_env_used_when_no_flag(lp, tmp_path, monkeypatch):
    monkeypatch.setenv("LEAN_PKG_REGISTRY", str(tmp_path / "env.yaml"))
    assert lp.registry_path(_args()) == tmp_path / "env.yaml"


def test_registry_defaults_to_cwd(lp, tmp_path, monkeypatch):
    monkeypatch.delenv("LEAN_PKG_REGISTRY", raising=False)
    monkeypatch.chdir(tmp_path)
    assert lp.registry_path(_args()) == tmp_path / "lean-packages.yaml"


def test_events_default_to_registry_sibling(lp, tmp_path, monkeypatch):
    monkeypatch.setenv("LEAN_PKG_REGISTRY", str(tmp_path / "sub" / "reg.yaml"))
    assert lp.events_path(_args()) == tmp_path / "sub" / "lean-package-events.jsonl"
    assert lp.events_path(_args(events=str(tmp_path / "e.jsonl"))) \
        == tmp_path / "e.jsonl"


def test_cli_uses_cwd_registry(env):
    p = subprocess.run([*CLI, "list", "--json"], cwd=env.registry.parent,
                       env=subprocess_env(), stdin=subprocess.DEVNULL,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert set(json.loads(p.stdout)["packages"]) == {"alpha", "beta"}


def test_cli_missing_default_registry_is_clean_refusal(tmp_path):
    p = subprocess.run([*CLI, "list"], cwd=tmp_path, env=subprocess_env(),
                       stdin=subprocess.DEVNULL, capture_output=True, text=True)
    assert p.returncode == 1
    assert "registry not found" in p.stderr and "lean-packages.yaml" in p.stderr


def test_notify_not_configured(lp, monkeypatch):
    monkeypatch.delenv("LEAN_PKG_NOTIFY_CMD", raising=False)
    assert lp.notify("hello") == "not-configured"


def test_notify_command_receives_message_on_stdin(lp, tmp_path, monkeypatch):
    out = tmp_path / "msg.txt"
    script = tmp_path / "sink.py"
    script.write_text("import sys, pathlib\n"
                      f"pathlib.Path({str(out)!r}).write_text(sys.stdin.read())\n")
    monkeypatch.setenv("LEAN_PKG_NOTIFY_CMD", f"{sys.executable} {script}")
    assert lp.notify("hello world") == "sent"
    assert out.read_text().strip() == "hello world"


def test_notify_command_failure(lp, monkeypatch):
    monkeypatch.setenv("LEAN_PKG_NOTIFY_CMD", f"{sys.executable} -c 'raise SystemExit(3)'")
    assert lp.notify("x") == "failed"
    monkeypatch.setenv("LEAN_PKG_NOTIFY_CMD", "/nonexistent/notifier")
    assert lp.notify("x") == "failed"


def test_promote_without_notify_cmd_says_not_configured(lp, env, fake_gh,
                                                        monkeypatch, capsys):
    monkeypatch.delenv("LEAN_PKG_NOTIFY_CMD", raising=False)
    rc = lp.main(["--registry", str(env.registry), "--json", "promote",
                  "beta", "--yes"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["notified"] is False
    assert [k for k in data if k.endswith("_sent")] == []
    assert read_reg(env)["packages"]["beta"]["status"] == "public"


def test_promote_text_reports_notify_not_configured(lp, env, fake_gh,
                                                    monkeypatch, capsys):
    monkeypatch.delenv("LEAN_PKG_NOTIFY_CMD", raising=False)
    assert lp.main(["--registry", str(env.registry), "promote", "beta", "--yes"]) == 0
    assert "notify command not configured" in capsys.readouterr().out


def test_promote_with_fake_notify_reports_notified(lp, env, fake_gh, fake_notify,
                                                   capsys):
    rc = lp.main(["--registry", str(env.registry), "--json", "promote",
                  "beta", "--yes"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["notified"] is True
    assert len(fake_notify) == 1

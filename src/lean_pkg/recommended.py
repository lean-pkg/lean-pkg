# lean-pkg recommended version -- the lean-pkg a shared registry recommends, against the one running now
"""A lab shares one registry; each machine runs its own lean-pkg. A machine still on an older release gates
and writes with it, and nothing said so (#388: a gate run with 0.20.0 while 0.22.0 was current). The registry
file declares the release its users should run, in the optional top-level key

    lean_pkg:
      recommended: X.Y.Z

written only by `lean-pkg registry recommend X.Y.Z` (event `registry-recommended`), never by hand. The check
is offline and reproducible: it reads the registry, never the network.

- Writing commands (every registry write, `cli.is_write_invocation`, and the receipt producers `receipt slurm`,
  `receipt systemd`, `receipt ci`, `receipt comparator`, outside their read-only forms) print ONE stderr notice
  when the running lean-pkg is older than the recommendation, and never refuse. A registry write's `--json`
  payload gains `lean_pkg_skew {running, recommended}`; the key is absent when the running lean-pkg is not
  older (equal, newer, no recommendation, an unreadable one).
- `doctor` prints a `recommended` line: warn when older, ok when equal or newer, n/a when the registry
  recommends nothing, warn when the key is malformed.
- `registry validate` refuses a malformed key: `lean_pkg` must be a mapping and `lean_pkg.recommended`, when
  present, a version string X.Y.Z (three non-negative integers without leading zeros). Other keys under
  `lean_pkg` are ignored, as an unknown optional key is (docs/formats.md), so a later release may add one.

The comparison is on X.Y.Z only. The running version's leading X.Y.Z is used, so an editable development
build (`0.23.0.dev1`, `0.23.0+dirty`) compares as 0.23.0; a running version with no leading X.Y.Z is never
older. The key is additive: the registry schema stays version 3, and an older lean-pkg ignores it and keeps
it on every save (it saves the mapping it loaded).
"""

from __future__ import annotations

import re
import shlex
import sys

import yaml

from . import core

KEY = "lean_pkg"
FIELD = "recommended"
EVENT = "registry-recommended"
VERSION_RE = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
_LEADING_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")
INSTALL_URL = "git+https://github.com/lean-pkg/lean-pkg.git"
# The receipt producers' forms that write nothing (a template, a preflight, an estimate, a read of attempts).
_RECEIPT_READ_ONLY_FLAGS = ("template", "check", "preflight_deps", "estimate")
# The notices printed during the current top-level invocation: `verify` runs its steps (receipts, the gate) as
# nested invocations of cli.main, and the notice is still one line. cli.main clears it when it is outermost.
_PRINTED: set[str] = set()


def parse(version) -> tuple[int, int, int] | None:
    """X.Y.Z of a recommendation (strict: the whole string), else None."""
    m = VERSION_RE.fullmatch(version) if isinstance(version, str) else None
    return tuple(int(x) for x in m.groups()) if m else None


def running_triple(version: str) -> tuple[int, int, int] | None:
    """The leading X.Y.Z of the running version (0.23.0.dev1 and 0.23.0+dirty compare as 0.23.0), else None."""
    m = _LEADING_RE.match(version or "")
    return tuple(int(x) for x in m.groups()) if m else None


def shape_errors(data) -> list[str]:
    """`registry validate`'s errors for the top-level `lean_pkg` key (none when absent)."""
    if not isinstance(data, dict) or KEY not in data:
        return []
    block = data[KEY]
    if not isinstance(block, dict):
        return [f"{KEY}: must be a mapping (e.g. {{{FIELD}: 0.23.0}}), got {block!r}"]
    if FIELD in block and parse(block[FIELD]) is None:
        return [f"{KEY}.{FIELD}: must be a version string X.Y.Z (three non-negative integers), got "
                f"{block[FIELD]!r} (set it with `lean-pkg registry recommend X.Y.Z`)"]
    return []


def recommended(data) -> str | None:
    """The registry's well-formed recommendation, else None (absent or malformed)."""
    block = data.get(KEY) if isinstance(data, dict) else None
    value = block.get(FIELD) if isinstance(block, dict) else None
    return value if parse(value) is not None else None


def skew(data, running: str | None = None) -> dict | None:
    """{"running", "recommended"} when the running lean-pkg is older than the registry's recommendation;
    None when it is not (equal, newer, nothing recommended, a malformed key, an unparseable running version)."""
    if running is None:
        from . import __version__ as running
    rec = recommended(data)
    run = running_triple(running)
    if rec is None or run is None or run >= parse(rec):
        return None
    return {"running": running, "recommended": rec}


def upgrade_route(rec: str) -> str:
    """How to upgrade, offline: `git -C <checkout> pull --ff-only` for an editable install (a git checkout),
    else the documented pipx install pinned to the recommended release's tag."""
    from . import cli   # install_detect lives there; cli imports this module only inside functions
    try:
        info = cli.install_detect(cli.package_dir())
    except Exception:   # noqa: BLE001 -- the route is advice; never let it break a write
        info = {"install": "unknown"}
    if info.get("install") == "editable":
        return f"git -C {shlex.quote(str(info['path']))} pull --ff-only"
    return f"pipx install --force '{INSTALL_URL}@v{rec}'"


def notice_line(sk: dict) -> str:
    return (f"lean-pkg: notice: this is lean-pkg {sk['running']}; the registry recommends {sk['recommended']} "
            f"(upgrade: {upgrade_route(sk['recommended'])})")


def notices_for(args) -> bool:
    """Does this invocation get the notice? Every registry write, and the receipt producers outside their
    read-only forms (`receipt check`, `--template`, `--check`, `--preflight-deps`, `--estimate`,
    `--verify-oleans`, `--attempts`)."""
    from . import cli
    if core.registry_snapshot_arg(args):
        return False
    if cli.is_write_invocation(args):
        return True
    if getattr(args, "cmd", None) != "receipt" or getattr(args, "receipt_cmd", None) not in (
            "slurm", "systemd", "ci", "comparator"):
        return False
    # `is True`: receipt comparator's --check is a path (the script to run), not the read-only mode
    if any(getattr(args, f, False) is True for f in _RECEIPT_READ_ONLY_FLAGS):
        return False
    return getattr(args, "verify_oleans", None) is None and getattr(args, "attempts", None) is None


def registry_top(args) -> dict | None:
    """The top-level keys of the registry this invocation read (core._LOADED_TOP), else a best-effort read of
    the resolved registry (a receipt producer reads none; a refusal may come before the load); None when
    unreadable. Never raises."""
    if core._LOADED_TOP:
        return dict(core._LOADED_TOP)
    try:
        p = core.registry_path(args)
        if not p.is_file():
            return None
        data = yaml.safe_load(p.read_text())
    except Exception:   # noqa: BLE001 -- best effort: an unreadable registry means no notice
        return None
    return data if isinstance(data, dict) else None


def after_command(args) -> dict | None:
    """run_command's step after a command: print the one stderr notice when this invocation gets it and the
    running lean-pkg is older than the registry's recommendation; returns the skew for the --json payload."""
    try:
        if not notices_for(args):
            return None
        sk = skew(registry_top(args))
        if sk is not None:
            line = notice_line(sk)
            if line not in _PRINTED:
                _PRINTED.add(line)
                print(line, file=sys.stderr)
        return sk
    except Exception:   # noqa: BLE001 -- a notice must never change a command's outcome
        return None


def doctor_line(data) -> tuple[str, str]:
    """(level, detail) of doctor's `recommended` line."""
    from . import __version__
    if not isinstance(data, dict):
        return "n/a", "registry unreadable; nothing compared"
    errs = shape_errors(data)
    if errs:
        return "warn", f"{errs[0]}; nothing compared (lean-pkg registry validate)"
    rec = recommended(data)
    if rec is None:
        return "n/a", ("the registry recommends no lean-pkg version (whoever upgrades the lab sets one: "
                       "lean-pkg registry recommend X.Y.Z)")
    sk = skew(data, __version__)
    if sk is not None:
        return "warn", (f"this is lean-pkg {__version__}, older than the {rec} the registry recommends "
                        f"(upgrade: {upgrade_route(rec)})")
    if running_triple(__version__) is None:
        return "n/a", f"the registry recommends {rec}; the running version {__version__!r} is not X.Y.Z"
    return "ok", f"lean-pkg {__version__}; the registry recommends {rec}"


def cmd_registry_recommend(args) -> int:
    """`registry recommend X.Y.Z`: set the registry's recommended lean-pkg (top-level lean_pkg.recommended)
    and append the log-only event registry-recommended {recommended, previous, reason}. The same version
    again writes nothing (exit 0)."""
    from . import __version__
    version = (args.version or "").strip()
    if parse(version) is None:
        raise core.UsageError(f"registry recommend: VERSION must be X.Y.Z (three non-negative integers, e.g. "
                              f"0.23.0), got {args.version!r}")
    reg_path, events_p = core.registry_path(args), core.events_path(args)
    data = core.load_registry(reg_path)
    block = data.get(KEY)
    previous = block.get(FIELD) if isinstance(block, dict) else None
    payload = {"ok": True, "recommended": version, "previous": previous, "registry": str(reg_path),
               "event": None, "running": __version__}
    if previous == version and isinstance(block, dict):
        line = f"the registry already recommends lean-pkg {version}; nothing written"
    else:
        new_block = dict(block) if isinstance(block, dict) else {}
        new_block[FIELD] = version
        data[KEY] = new_block
        core.save_registry(reg_path, data)
        core._LOADED_TOP[KEY] = new_block
        fields = {"recommended": version, "previous": previous}
        if (args.reason or "").strip():
            fields["reason"] = args.reason.strip()
        core.append_event(events_p, core.now_iso(), None, EVENT, **fields)
        payload["event"] = EVENT
        line = (f"the registry recommends lean-pkg {version}" + (f" (was {previous})" if previous else "")
                + f"; writing commands of an older lean-pkg print a notice (this is {__version__})")
    if args.json_out:
        core.print_json(payload)
    else:
        print(line)
    return 0


def add_parser(registry_sub, add_json) -> None:
    s = registry_sub.add_parser(
        "recommend", help="set the lean-pkg version this registry recommends; an older lean-pkg's writing "
                          "commands then print a notice (offline; never a refusal)")
    s.add_argument("version", metavar="VERSION",
                   help="X.Y.Z: the release every machine using this registry should run (stored as the "
                        "top-level lean_pkg.recommended; event registry-recommended)")
    s.add_argument("--reason", default="", help="free text recorded with the event")
    add_json(s)
    s.set_defaults(fn=cmd_registry_recommend)

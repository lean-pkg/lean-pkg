# lean-pkg container -- the container image a verification job ran in, as an additive receipt key
"""The additive receipt key `container` (#236): the identity of the container image the job's tools ran in.

    container:
      engine: apptainer | singularity | podman | docker
      image_sha256: <64 hex>          # Apptainer / Singularity: the SHA-256 of the SIF file
      oci_digest: sha256:<64 hex>     # podman / docker: the image's OCI digest (exactly one of the two)
      ref: TEXT                       # the image path or name as given, a home directory shown as ~

Where it comes from:

- `receipt slurm` (and `receipt systemd`, which shares the pipeline): the verify log's `container-engine:`,
  `container-image:` and `container-image-sha256:` (or `container-oci-digest:`) lines, which the
  `--template` job writes when it runs its tools in a container (CONTAINER_IMAGE, or a `.sif` word of
  RUN). With --stdout, the job stdout must carry the same digest line (`container-image-sha256 <hex>`),
  so an edited stdout line and an edited log line both refuse (the log is sealed by verify-log-sha256).
- `comparator-receipt` / `receipt comparator`: inside Apptainer or Singularity, from APPTAINER_CONTAINER
  (else SINGULARITY_CONTAINER) when it names a regular file readable here, hashed when the receipt is
  written. A sandbox directory, an image path not visible inside the container, or an OCI runtime (no
  digest is visible from inside one) leaves the key absent.

No schema bump: gate and every reader that does not know the key ignore it; a receipt without it is
unchanged. `receipt check` shows it and fails a malformed one; the page names the engine and the digest,
never `ref`. The digest is what the job (or the runner) measured; nothing published lets a referee
re-derive it, because the image is not published.
"""

from __future__ import annotations

import hashlib
import os
import re

SIF_ENGINES = ("apptainer", "singularity")
OCI_ENGINES = ("podman", "docker")
ENGINES = SIF_ENGINES + OCI_ENGINES
# verify-log key -> (receipt key, engines it belongs to, value pattern)
DIGEST_KEYS = {
    "container-image-sha256": ("image_sha256", SIF_ENGINES, re.compile(r"[0-9a-f]{64}")),
    "container-oci-digest": ("oci_digest", OCI_ENGINES, re.compile(r"sha256:[0-9a-f]{64}")),
}
ENGINE_KEY, REF_KEY = "container-engine", "container-image"
LOG_KEYS = (ENGINE_KEY, REF_KEY, *DIGEST_KEYS)
LABELS = {"apptainer": "Apptainer", "singularity": "Singularity", "podman": "Podman", "docker": "Docker"}
REF_MAX = 300
_HOME_RE = re.compile(r"^(?:/home|/Users)/[^/\s]+(?=/)|^/root(?=/)")


def redact_ref(ref: str) -> str:
    """The image path as given, with a home directory (this one, or /home/<u>, /Users/<u>, /root) shown as ~."""
    home = os.environ.get("HOME") or ""
    if home not in ("", "/") and (ref == home or ref.startswith(home.rstrip("/") + "/")):
        ref = "~" + ref[len(home.rstrip("/")):]
    return _HOME_RE.sub("~", ref, count=1)


def problems(value) -> list[str]:
    """What is wrong with a receipt's `container` value (empty: well formed)."""
    if not isinstance(value, dict):
        return ["container: must be a mapping"]
    out = []
    engine = value.get("engine")
    if engine not in ENGINES:
        out.append(f"container.engine: must be one of {', '.join(ENGINES)}, got {engine!r}")
    held = [(k, rk, eng, rx) for k, (rk, eng, rx) in DIGEST_KEYS.items() if rk in value]
    if len(held) != 1:
        out.append("container: exactly one of image_sha256 (a SIF file) and oci_digest (an OCI image)")
    for _k, rk, eng, rx in held:
        if not isinstance(value[rk], str) or not rx.fullmatch(value[rk]):
            out.append(f"container.{rk}: must match {rx.pattern}")
        if engine in ENGINES and engine not in eng:
            out.append(f"container.{rk}: only for engine {' or '.join(eng)}, not {engine}")
    ref = value.get("ref")
    if ref is not None and (not isinstance(ref, str) or not ref.strip() or "\n" in ref or len(ref) > REF_MAX):
        out.append(f"container.ref: one non-empty line of at most {REF_MAX} characters")
    extra = sorted(set(value) - {"engine", "ref", *(rk for rk, _e, _r in DIGEST_KEYS.values())})
    if extra:
        out.append(f"container: unknown key(s) {', '.join(map(repr, extra))}")
    return out


def digest_of(value: dict) -> tuple[str, str]:
    """(kind, digest) of a well-formed value: ('SIF SHA-256', hex) or ('OCI digest', sha256:hex)."""
    if "image_sha256" in value:
        return "SIF SHA-256", value["image_sha256"]
    return "OCI digest", value["oci_digest"]


def describe(value: dict) -> str:
    """One line for `receipt check` and the receipt's check list."""
    kind, digest = digest_of(value)
    ref = f" (image `{value['ref']}`)" if value.get("ref") else ""
    return f"{LABELS.get(value['engine'], value['engine'])} container, {kind} {digest}{ref}"


def from_log(fields: dict, stdout_values, refusals: list[str], checks: list[str]) -> dict | None:
    """The `container` value of a Slurm / systemd receipt from the verify log's container lines (`fields`, the
    parsed log), cross-checked with the job stdout's digest line when there is a stdout (`stdout_values(key)`,
    the stdout's values for a key, else None). Returns None when neither names a container."""
    in_log = [k for k in DIGEST_KEYS if k in fields]
    on_stdout = {k: stdout_values(k) for k in DIGEST_KEYS} if stdout_values is not None else {}
    if not in_log and not any(on_stdout.values()) and ENGINE_KEY not in fields and REF_KEY not in fields:
        return None
    n0 = len(refusals)
    if len(in_log) != 1:
        refusals.append("verify log: container lines need exactly one of `container-image-sha256:` (a SIF "
                        "file) and `container-oci-digest:` (an OCI image)"
                        + (f"; the job stdout has {', '.join(f'`{k}`' for k, v in on_stdout.items() if v)}"
                           if not in_log and any(on_stdout.values()) else ""))
        return None
    key = in_log[0]
    rkey, engines, rx = DIGEST_KEYS[key]
    digest = fields[key].strip().lower()
    engine = (fields.get(ENGINE_KEY) or "").strip()
    if not rx.fullmatch(digest):
        refusals.append(f"verify log: `{key}:` must match {rx.pattern}, got {fields[key]!r}")
    if engine not in engines:
        refusals.append(f"verify log: `{ENGINE_KEY}:` must be one of {', '.join(engines)} beside `{key}:`, "
                        f"got {engine or 'nothing'!r}")
    sealed = "in-log seal only (no --stdout)"
    if stdout_values is not None:
        for other in DIGEST_KEYS:
            if other != key and on_stdout.get(other):
                refusals.append(f"job stdout: a `{other}` line, but the verify log records `{key}:`")
        vals = [v.lower() for v in on_stdout.get(key) or []]
        if len(vals) != 1:
            refusals.append(f"job stdout: {len(vals)} `{key}` line(s) beside the verify log's `{key}:` line; "
                            f"exactly one is required")
        elif vals[0] != digest:
            refusals.append(f"job stdout: `{key}` {vals[0][:19]}… is not the verify log's `{key}:` "
                            f"{digest[:19]}…; the image digest was changed after the job")
        sealed = "verify log = job stdout"
    if len(refusals) > n0:
        return None
    value = {"engine": engine, rkey: digest}
    ref = fields.get(REF_KEY)
    if ref:
        value["ref"] = redact_ref(ref.strip())
    bad = problems(value)
    if bad:
        refusals += [f"verify log: {b}" for b in bad]
        return None
    checks.append(f"✓ container: {describe(value)}; {sealed}")
    return value


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def from_environment(environ=None) -> dict | None:
    """The `container` value a process running inside Apptainer / Singularity can measure itself: the SIF
    named by APPTAINER_CONTAINER (else SINGULARITY_CONTAINER), hashed, when it is a regular file readable
    here. None otherwise (not in a container, a sandbox directory, the image not visible inside, an OCI
    runtime)."""
    env = os.environ if environ is None else environ
    for var, engine in (("APPTAINER_CONTAINER", "apptainer"), ("SINGULARITY_CONTAINER", "singularity")):
        path = env.get(var)
        if not path:
            continue
        try:
            if not os.path.isfile(path):
                return None
            digest = sha256_file(path)
        except OSError:
            return None
        return {"engine": engine, "image_sha256": digest, "ref": redact_ref(path)}
    return None

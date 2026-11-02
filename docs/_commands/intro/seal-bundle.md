| Form | What it does | Key flags |
|---|---|---|
| `seal-bundle --spec S --receipt KEY=R ... --pilot F --out DIR` | render a seal job's bundle (a later job that re-checks a commit a receipt verified: kernel replay, fresh rebuild, axiom audit) into the new directory `DIR`: the job script filled from the package's template, `source.tgz`, one module list per package (plus the joined list), the pilot list, the spec's helper files, and `INPUT-SHA256SUMS` over all of them (in that order, the script last). Every value is derived: the commit is the first package's receipt `commit`, the tree `git rev-parse COMMIT:./` of `--repo`; each other package's receipt must name a commit whose tree of that package equals the bundled one apart from `.lean-receipts/` (its own receipt, committed after its gate), else a refusal naming the differing paths; each module list is the umbrella's `import` lines at the commit, which must equal the modules of the `.lean` files under the umbrella's directory (sorted); each count is the one value its pattern's group gives in that package's sealed verify log (the log beside the receipt or in `.lean-receipts/` whose bytes hash to `report_sha256`; no value or two different values refuse); each dependency revision is the 40-hex `rev` of that package in `lake-manifest.json` committed at the commit; `source.tgz` is `git archive --mtime=@<commit time> COMMIT:<prefix>` gzipped with no name and time 0 (the same commit gives the same bytes; git 2.43 or newer), refused unless it holds exactly the tree's files. Each receipt is checked at its own commit, not at HEAD (a seal comes after the fact): schema and fields, a full commit that exists, `lean_toolchain` and `mathlib_rev` equal to what that commit pins, `sorry_count` 0, axioms within the default three. Writes only `DIR` (which must not exist): never a receipt, the registry or the event log | `--spec PATH` (a `seal-bundle/1` YAML file; its `template` and `files` are relative to it); `--receipt KEY=PATH` (once per package of the spec); `--repo DIR` (the package directory archived, default `.`; package `dir`s are relative to it); `--pilot FILE` (the modules the pilot run replays, one per line; required with `--out`: a pilot is a choice, never guessed); `--note TEXT` (one line, at most 200 characters, filled into `@@NOTE@@`); `--json` |
| `seal-bundle ... --check DIR` | re-derive the bundle and compare an existing one in `DIR` file by file (a hand-made bundle, before a rendered one is trusted): each difference is listed with a unified diff, exit 1; files `DIR` has beyond the bundle are listed as not compared. Without `--pilot`, `DIR`'s own pilot list is used (and noted). A `source.tgz` made elsewhere is not rebuilt byte for byte (a bare tree archive carries the time it was made), so its hash is taken from `DIR`'s `INPUT-SHA256SUMS` for the script and the sums, and said so; the script binds the tree, which is derived | `--check DIR` (instead of `--out`) |

The spec (`seal-bundle/1`; an unknown key is refused, not ignored):

```yaml
schema: seal-bundle/1
template: gate.sbatch.in            # the package's job script with @@NAME@@ placeholders
packages:                           # the first one is the commit bundled
  - {key: main, dir: ".", umbrella: MyProof.lean, list: main-modules.txt}
  - {key: contrib, dir: contrib, umbrella: MyProofContrib.lean, list: contrib-modules.txt}
all_list: modules-all.txt           # optional: the lists joined, in package order
counts:                             # one capture group; one distinct value in the log
  - {var: NTHEOREMS_MAIN, package: main, pattern: 'PASS: (\d+) exact theorem names'}
deps: {MATHLIB_REV: mathlib}        # VAR: the lake-manifest.json package name
files: [verify-replay.py]           # copied into the bundle and hashed
# script: gate.sbatch   source: source.tgz   sums: INPUT-SHA256SUMS   pilot_list: modules-pilot.txt
```

Placeholders: `@@COMMIT@@` and `@@TREE@@` (both required: a job that binds neither binds nothing),
`@@COMMIT_SHORT@@` (7 characters), `@@TREE_SHORT@@` (8), `@@COMMIT:KEY@@` and `@@COMMIT_SHORT:KEY@@` (the
commit of that package's receipt), `@@SHA256:FILE@@` (any bundle file but the script and the sums),
`@@NOTE@@`, and the spec's `counts` and `deps` variables. An unknown or unfilled placeholder is refused, all
at once, and nothing is written. The template is the package's: lean-pkg knows no cluster, so a site's
partition, image and paths are written in it as they are. `--json`: `{ok, mode (write|check), out, commit,
tree, packages[{key, dir, commit, receipt, modules}], counts{}, deps{}, pilot[], files[{name, sha256}],
notes[], differences[{file, kind (missing|differs), diff[]}]}`. Exit 0 written or equal, 1 refused or a
difference, 2 usage (`--pilot` missing with `--out`, a `--receipt` missing, unknown or given twice, a bad
`--note`).

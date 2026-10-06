# Contributing to lean-pkg

Thank you for helping. This repository holds the `lean-pkg` tool. A public
catalog of packages will be added later. The rules below follow
Mathlib's contribution norms, including its rules on AI assistance.

## AI assistance rules

1. **Disclosure is mandatory.** If an AI system helped write code, tests,
   documentation or a Lean proof in your pull request, say so in the PR
   description. Name the model or tool and the parts it touched. "No AI
   assistance" is also a valid statement, and we ask for it explicitly.
2. **Use your own words in review threads.** Write review comments and
   replies yourself. Do not paste generated text into a discussion as if it
   were your own reasoning.
3. **You must understand what you submit.** You are responsible for every
   line. Be ready to explain any change and to defend it under review without
   re-asking a model.
4. **New contributors: one open pull request at a time.** Wait until your
   first PR is merged or closed before opening the next one.

## Pull request flow

Contributions are pull requests against `main`.

- **Tool changes** (code, tests, docs): keep each PR to one logical change.
  Every PR must pass `make check` (tests, private-boundary check, tab
  scan, version agreement). The maintainer runs it locally before merging
  and a clone can enforce it on push by opting into the hook directory
  (see `RELEASING.md`); the same targets run as hosted CI once the
  repository is public (GitHub Actions is free there).
- **Index entries**: a PR adds `index/<name>/` (an `entry.yaml` of schema
  `index-entry/1` and the verbatim receipt copies under `receipts/`) written
  by `lean-pkg index add`, plus the re-rendered `index/README.md`. Code and
  logs stay in the package repository, not here. Before merging, the
  maintainer runs `lean-pkg index check --clone <name>`, which clones the
  package at the pin, re-derives the receipts and logs from bytes, checks the
  toolchain, `lake-manifest.json`, license and tag at the pin, and recomputes
  the trust level; its output goes into the review. A PR cannot merge without
  a passing run. See [The index](docs/index-catalog.md).
- **Human review** covers what machines cannot check: whether the Lean
  statement says what the paper says, naming, and documentation. Reviewers
  do not have to be maintainers.

## Local checks

```bash
python3 -m pip install -e '.[test]'
python3 -m pytest -q
bash scripts/private-boundary-check.sh
```

Use spaces, not tab characters, in source and documentation files.

## Code of conduct

We follow the norms of the Lean community.

## License

By contributing, you agree that your contribution is licensed under
Apache-2.0, the license of this repository.

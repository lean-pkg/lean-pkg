# lean-pkg: local gates. No tab characters: recipes use '>' as the prefix.
# `make check` is the release gate; hosted CI runs the same targets once the repository is public.
.RECIPEPREFIX := >
.PHONY: check test boundary tabs versions changelog reuse history public-safety release release-version cut-release record-doi docs goldens

check: test boundary tabs versions changelog reuse
> @echo "check: all gates green"

# A private bytecode cache per run: concurrent runs never share pytest's rewritten .pyc files.
# The EXIT trap removes it on every exit, including Ctrl-C (INT/TERM/HUP exit through it).
test:
> @d=$$(mktemp -d) && trap 'rm -rf "$$d"' EXIT && trap 'exit 130' INT TERM HUP && PYTHONPYCACHEPREFIX="$$d" python3 -m pytest -q -p no:cacheprovider

# Re-record the golden CLI contract (help texts, --json key sets) after an intended change; review
# `git diff tests/golden` before committing (CONTRIBUTING.md, "The golden CLI contract").
goldens:
> @d=$$(mktemp -d) && trap 'rm -rf "$$d"' EXIT && trap 'exit 130' INT TERM HUP && PYTHONPYCACHEPREFIX="$$d" LEAN_PKG_RECORD_GOLDENS=1 python3 -m pytest -q -p no:cacheprovider tests/test_cli_contract_help.py tests/test_cli_contract_json.py

boundary:
> bash scripts/private-boundary-check.sh

tabs:
> @if grep -rnP '\t' --include='*.md' --include='*.py' --include='*.yml' --include='*.yaml' --include='*.cff' --include='*.sh' --include='*.txt' --include='*.svg' --include='*.css' --include='*.js' --include='*.html' --include='Makefile' . --exclude-dir=.git --exclude-dir=.lake --exclude-dir=.venv | grep -v '^Binary' | head -5 | grep .; then echo "tabs: tab characters found (see above)"; exit 1; else echo "tabs: none"; fi

versions:
> python3 scripts/check-versions.py

# Each released `## [X.Y.Z]` section reads as it did at tag vX.Y.Z (record-doi's DOI line aside); no network.
# Then the category check: `## [Unreleased]` agrees with the in-progress upgrade record (a no_author_change
# PR's entry is never under ### Added; RELEASING.md "When to tag").
changelog:
> python3 scripts/check-changelog-releases.py
> python3 scripts/release-version.py --check

# REUSE 3.3 compliance (gate G3 of the public-flip checklist); skipped, not passed, when the tool is absent.
reuse:
> @if command -v reuse >/dev/null 2>&1; then reuse lint -q && echo "reuse: compliant"; else echo "reuse: SKIPPED (pipx install reuse)"; fi

# Every blob and commit message reachable from any ref. Not part of `check`: a hit here is fixed
# only by rewriting history, which is a maintainer decision before the public flip (RELEASING.md).
history:
> bash scripts/private-boundary-check.sh --history

# lean-pkg's own public-safety scan (RELEASING.md): a fresh clone carrying every ref, registered as
# lean-pkg-self in a throwaway registry, scanned with this checkout's code, history included, --strict.
# Needs the untracked .lean-pkg-private-patterns at the repository root; never touches a real registry.
public-safety:
> @test -f .lean-pkg-private-patterns || { echo "public-safety: .lean-pkg-private-patterns missing at the repository root (untracked; see RELEASING.md)"; exit 2; }
> @d=$$(mktemp -d) && trap 'rm -rf "$$d"' EXIT && trap 'exit 130' INT TERM HUP && \
>  git clone -q . "$$d/lean-pkg" && git -C "$$d/lean-pkg" fetch -q origin '+refs/*:refs/selfscan/*' && \
>  cp .lean-pkg-private-patterns "$$d/lean-pkg/" && \
>  export LEAN_PKG_REGISTRY="$$d/lean-packages.yaml" PYTHONPATH="$$PWD/src" && \
>  unset LEAN_PKG_COMMIT LEAN_PKG_PUSH LEAN_PKG_EVENTS LEAN_PKG_INDEX LEAN_PKG_HOME LEAN_PKG_REGISTRY_SNAPSHOT && \
>  python3 -m lean_pkg.cli init >/dev/null && \
>  python3 -m lean_pkg.cli register lean-pkg-self --repo https://github.com/lean-pkg/lean-pkg --path "$$d/lean-pkg" --owner-project lean-pkg --status scratch >/dev/null && \
>  python3 -m lean_pkg.cli public-safety lean-pkg-self --history --strict

# Every generated file, rewritten from its source: llms.txt and docs/llms.txt (cli.llms_txt),
# index/README.md (index/*/entry.yaml), the command reference docs/commands/*.md (the argparse
# parsers plus docs/_commands/; scripts/command_docs.py) and the packaged stack copies
# src/lean_pkg/data/stacks/ (docs/stacks/lts-*.yaml is the source of truth). tests/test_docs.py,
# tests/test_index_render.py, tests/test_command_docs.py and tests/test_stacks.py compare the committed
# files to the same output; CI runs `make docs`
# then `git diff --exit-code` (#81).
docs:
> PYTHONPATH=src python3 -c 'import pathlib; from lean_pkg.cli import llms_txt; t = llms_txt(); [pathlib.Path(p).write_text(t) for p in ("llms.txt", "docs/llms.txt")]'
> PYTHONPATH=src python3 -m lean_pkg.cli index render --index index
> python3 scripts/command_docs.py
> @if [ -d docs/stacks ]; then rm -f src/lean_pkg/data/stacks/lts-*.yaml && cp docs/stacks/lts-*.yaml src/lean_pkg/data/stacks/; fi

# make release VERSION=0.1.0  (runs scripts/release.sh; see RELEASING.md)
release:
> bash scripts/release.sh "$(VERSION)"

# make release-version [MILESTONE=Mk]  (the version RELEASING.md "When to tag" requires for the next release,
# derived from the in-progress upgrade record and `## [Unreleased]`; read-only; scripts/release-version.py)
release-version:
> python3 scripts/release-version.py $(if $(MILESTONE),--milestone "$(MILESTONE)")

# make cut-release VERSION=0.1.0 [MILESTONE=Mk]  (the whole RELEASING.md checklist, steps 0 to 7, resumable;
# refuses a VERSION other than `make release-version`'s; DOI_WAIT=seconds to poll Zenodo, default 300;
# see scripts/cut-release.py)
cut-release:
> python3 scripts/cut-release.py "$(VERSION)" --doi-wait "$(or $(DOI_WAIT),300)" $(if $(MILESTONE),--milestone "$(MILESTONE)")

# make record-doi VERSION=0.1.0  (step 7 alone: the Zenodo version DOI into CITATION.cff and CHANGELOG.md)
record-doi:
> python3 scripts/record-doi.py "$(VERSION)" --wait "$(or $(DOI_WAIT),0)"

# lean-pkg: local gates. No tab characters: recipes use '>' as the prefix.
# `make check` is the release gate; hosted CI runs the same targets once the repository is public.
.RECIPEPREFIX := >
.PHONY: check test boundary tabs versions reuse history release

check: test boundary tabs versions reuse
> @echo "check: all gates green"

# A private bytecode cache per run: concurrent runs never share pytest's rewritten .pyc files.
test:
> @d=$$(mktemp -d) && PYTHONPYCACHEPREFIX="$$d" python3 -m pytest -q -p no:cacheprovider; rc=$$?; rm -rf "$$d"; exit $$rc

boundary:
> bash scripts/private-boundary-check.sh

tabs:
> @if grep -rnP '\t' --include='*.md' --include='*.py' --include='*.yml' --include='*.yaml' --include='*.cff' --include='*.sh' --include='*.txt' --include='*.svg' --include='*.css' --include='*.js' --include='*.html' --include='Makefile' . --exclude-dir=.git --exclude-dir=.lake --exclude-dir=.venv | grep -v '^Binary' | head -5 | grep .; then echo "tabs: tab characters found (see above)"; exit 1; else echo "tabs: none"; fi

versions:
> python3 scripts/check-versions.py

# REUSE 3.3 compliance (gate G3 of the public-flip checklist); skipped, not passed, when the tool is absent.
reuse:
> @if command -v reuse >/dev/null 2>&1; then reuse lint -q && echo "reuse: compliant"; else echo "reuse: SKIPPED (pipx install reuse)"; fi

# Every blob and commit message reachable from any ref. Not part of `check`: a hit here is fixed
# only by rewriting history, which is a maintainer decision before the public flip (RELEASING.md).
history:
> bash scripts/private-boundary-check.sh --history

# make release VERSION=0.1.0  (runs scripts/release.sh; see RELEASING.md)
release:
> bash scripts/release.sh "$(VERSION)"

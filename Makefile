# lean-pkg: local gates. No tab characters: recipes use '>' as the prefix.
# `make check` is the release gate; hosted CI runs the same targets once the repository is public.
.RECIPEPREFIX := >
.PHONY: check test boundary tabs versions release

check: test boundary tabs versions
> @echo "check: all gates green"

test:
> python3 -m pytest -q -p no:cacheprovider

boundary:
> bash scripts/private-boundary-check.sh

tabs:
> @if grep -rnP '\t' --include='*.md' --include='*.py' --include='*.yml' --include='*.yaml' --include='*.cff' --include='*.sh' --include='*.txt' --include='*.svg' --include='*.css' --include='*.js' --include='*.html' --include='Makefile' . --exclude-dir=.git --exclude-dir=.lake --exclude-dir=.venv | grep -v '^Binary' | head -5 | grep .; then echo "tabs: tab characters found (see above)"; exit 1; else echo "tabs: none"; fi

versions:
> python3 scripts/check-versions.py

# make release VERSION=0.1.0  (runs scripts/release.sh; see RELEASING.md)
release:
> bash scripts/release.sh "$(VERSION)"

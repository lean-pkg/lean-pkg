#!/usr/bin/env python3
"""The PyPI metadata: the long description PyPI renders, the project URLs and the classifiers."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())
PROJECT = PYPROJECT["project"]
LINK = re.compile(r"\]\(([^)\s]+)\)|(?:src|href)=\"([^\"]+)\"")


def test_the_long_description_is_pypi_md():
    """PyPI shows the readme without the repository around it: README.md's relative links and images
    (docs/quickstart.md, docs/assets/...) would all be broken there."""
    assert PROJECT["readme"] == "PYPI.md"


def test_every_link_on_the_pypi_page_is_absolute():
    text = (ROOT / "PYPI.md").read_text()
    links = [a or b for a, b in LINK.findall(text)]
    assert links, "PYPI.md has no links"
    relative = [u for u in links if not u.startswith("https://")]
    assert relative == [], "PyPI cannot resolve relative links: " + ", ".join(relative)


def test_the_pypi_page_names_the_commands_it_shows():
    """Every `lean-pkg X` the page shows is a real top-level command."""
    from lean_pkg import cli_parser
    ap = cli_parser.build_parser()
    sub = next(a for a in ap._actions if a.__class__.__name__ == "_SubParsersAction")
    shown = set(re.findall(r"^lean-pkg ([a-z][a-z-]*)", (ROOT / "PYPI.md").read_text(), re.M))
    assert shown and shown <= set(sub.choices), sorted(shown - set(sub.choices))


def test_project_urls():
    urls = PROJECT["urls"]
    for key in ("Homepage", "Documentation", "Source", "Changelog", "Issues"):
        assert urls[key].startswith("https://"), key


def test_python_classifiers_match_requires_python_and_ci():
    """One Python list: requires-python's floor, the classifiers and the CI matrix agree."""
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    matrix = ci["jobs"]["test"]["strategy"]["matrix"]["python-version"]
    classified = sorted(c.rsplit(" :: ", 1)[1] for c in PROJECT["classifiers"]
                        if re.fullmatch(r"Programming Language :: Python :: 3\.\d+", c))
    key = lambda v: tuple(map(int, v.split(".")))
    assert sorted(classified, key=key) == sorted(map(str, matrix), key=key)
    assert PROJECT["requires-python"] == f">={min(classified, key=key)}"

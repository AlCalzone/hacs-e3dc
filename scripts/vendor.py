#!/usr/bin/env python3
"""
Rebuild custom_components/e3dc from the core integration and its library.

    scripts/vendor.py --library ../e3dc-ess-modbus --integration ../ha-core-e3dc
    scripts/vendor.py --version 0.1.0b1

The device library is copied into the integration as a subpackage, and the
integration imports it relatively, because the library is not on PyPI yet.
modbus-connection comes with Home Assistant's own modbus integration.

Both source checkouts must be clean. The commits vendored are written to
VENDORED. Without ``--version``, the manifest keeps its current version.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "custom_components" / "e3dc"
# tests/conftest.py holds the fixtures core keeps outside the integration.
TESTS = ROOT / "tests" / "e3dc"
REPOSITORY = "https://github.com/AlCalzone/hacs-e3dc"
# The common strings of the oldest Home Assistant release hacs.json allows
CORE_STRINGS = (
    "https://raw.githubusercontent.com/home-assistant/core/{version}/"
    "homeassistant/strings.json"
)
# Files a custom integration ships without.
SKIPPED = {"quality_scale.yaml", "strings.json"}
# How the core tests' imports read for a custom integration.
TEST_IMPORTS = (
    (r"homeassistant\.components\.e3dc\b", "custom_components.e3dc"),
    (r"^from e3dc_ess_modbus", "from custom_components.e3dc.e3dc_ess_modbus"),
    (r"^from tests\.", "from pytest_homeassistant_custom_component."),
)
KEY_REFERENCE = re.compile(r"\[%key:([a-z0-9_:]+)%\]")


def _lookup(tree: dict, path: list[str]) -> str:
    """Follow a ``::``-separated translation key through nested dicts."""
    node: object = tree
    for part in path:
        if not isinstance(node, dict):
            raise TypeError(path)
        node = node[part]
    if not isinstance(node, str):
        raise TypeError(path)
    return node


def _resolve(strings: dict, common: dict) -> dict:
    """Replace every ``[%key:...%]`` reference with the text it names."""

    def resolve_text(text: str) -> str:
        def replace(match: re.Match[str]) -> str:
            scope, *path = match.group(1).split("::")
            if scope == "common":
                return resolve_text(_lookup(common["common"], path))
            if scope != "component" or path[0] != "e3dc":
                raise ValueError(match.group(0))
            return resolve_text(_lookup(strings, path[1:]))

        return KEY_REFERENCE.sub(replace, text)

    def walk(node: object) -> object:
        if isinstance(node, dict):
            return {key: walk(value) for key, value in node.items()}
        if isinstance(node, str):
            return resolve_text(node)
        return node

    resolved = walk(strings)
    if not isinstance(resolved, dict):
        raise TypeError(resolved)
    return resolved


def _vendor_integration(source: Path) -> None:
    """Copy the core integration, importing the library relatively."""
    for path in sorted(source.iterdir()):
        if path.name in SKIPPED or path.name in ("__pycache__", "translations"):
            continue
        text = path.read_text()
        if path.suffix == ".py":
            text = re.sub(
                r"^from e3dc_ess_modbus",
                "from .e3dc_ess_modbus",
                text,
                flags=re.MULTILINE,
            )
        (TARGET / path.name).write_text(text)


def _vendor_library(source: Path) -> None:
    """Copy the device library in as a subpackage."""
    shutil.copytree(
        source,
        TARGET / "e3dc_ess_modbus",
        ignore=shutil.ignore_patterns("__pycache__"),
    )


def _vendor_tests(source: Path) -> None:
    """Copy the core tests, pointed at the custom integration."""
    shutil.copytree(source, TESTS, ignore=shutil.ignore_patterns("__pycache__"))
    for path in TESTS.glob("*.py"):
        text = path.read_text()
        for pattern, replacement in TEST_IMPORTS:
            text = re.sub(pattern, replacement, text, flags=re.MULTILINE)
        path.write_text(text)


def _write_manifest(version: str) -> None:
    """Turn core's manifest into a custom integration's."""
    path = TARGET / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest.pop("quality_scale", None)
    manifest["documentation"] = REPOSITORY
    manifest["issue_tracker"] = f"{REPOSITORY}/issues"
    # The vendored library logs under the integration's package.
    manifest["loggers"] = [
        f"custom_components.e3dc.{logger}" if logger == "e3dc_ess_modbus" else logger
        for logger in manifest["loggers"]
    ]
    manifest["requirements"] = []
    manifest["version"] = version
    # Hassfest wants domain and name first, then the other keys sorted.
    ordered = {key: manifest[key] for key in ("domain", "name")}
    ordered.update(sorted((k, v) for k, v in manifest.items() if k not in ordered))
    path.write_text(json.dumps(ordered, indent=2) + "\n")


def _write_translations(source: Path) -> None:
    """Resolve strings.json into the English translation Home Assistant loads."""
    strings = json.loads((source / "strings.json").read_text())
    minimum = json.loads((ROOT / "hacs.json").read_text())["homeassistant"]
    url = CORE_STRINGS.format(version=minimum)
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310
        common = json.load(response)
    translations = TARGET / "translations"
    translations.mkdir()
    (translations / "en.json").write_text(
        json.dumps(_resolve(strings, common), indent=2, ensure_ascii=False) + "\n"
    )


def _clean_commit(path: Path) -> str:
    """Return the commit a source checkout is at, or exit if it is not clean."""

    def git(*args: str) -> str:
        return subprocess.run(  # noqa: S603
            ["git", "-C", str(path), *args],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    if git("status", "--porcelain"):
        sys.exit(f"{path} has uncommitted changes")
    return git("rev-parse", "HEAD")


def main() -> None:
    """Rebuild the custom integration."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--library", type=Path, default=ROOT.parent / "e3dc-ess-modbus")
    parser.add_argument(
        "--integration", type=Path, default=ROOT.parent / "ha-core-e3dc"
    )
    parser.add_argument("--version", help="the version to write into the manifest")
    args = parser.parse_args()

    sources = {"e3dc-ess-modbus": args.library, "ha-core-e3dc": args.integration}
    commits = {name: _clean_commit(path) for name, path in sources.items()}

    version = args.version or "0.0.0"
    if args.version is None and (manifest := TARGET / "manifest.json").exists():
        version = json.loads(manifest.read_text())["version"]

    integration = args.integration / "homeassistant" / "components" / "e3dc"
    shutil.rmtree(TARGET, ignore_errors=True)
    shutil.rmtree(TESTS, ignore_errors=True)
    TARGET.mkdir(parents=True)
    _vendor_integration(integration)
    _vendor_tests(args.integration / "tests" / "components" / "e3dc")
    _vendor_library(args.library / "src" / "e3dc_ess_modbus")
    _write_manifest(version)
    _write_translations(integration)
    # The rewritten imports sort differently from the originals.
    ruff = [sys.executable, "-m", "ruff"]
    subprocess.run(  # noqa: S603
        [*ruff, "check", "--select", "I", "--fix", "--quiet", TARGET, TESTS],
        check=True,
    )
    subprocess.run([*ruff, "format", "--quiet", TARGET, TESTS], check=True)  # noqa: S603
    (ROOT / "VENDORED").write_text(
        "".join(f"{name} {commit}\n" for name, commit in commits.items())
    )


if __name__ == "__main__":
    main()

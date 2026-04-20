from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BLUEPRINT_ROOT = REPO_ROOT / "docs" / "blueprint"
HANGUL_PATTERN = re.compile(r"[가-힣]")
MARKDOWN_LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def _iter_unsuffixed_blueprint_docs() -> list[Path]:
    return sorted(
        path
        for path in BLUEPRINT_ROOT.rglob("*.md")
        if not path.name.endswith(".ko.md") and not path.name.endswith("_ko.md")
    )


def _ko_mirror_for(path: Path) -> Path:
    return path.with_name(f"{path.stem}.ko.md")


def _iter_local_markdown_links(path: Path) -> list[str]:
    text = path.read_text()
    links: list[str] = []
    for match in MARKDOWN_LINK_PATTERN.finditer(text):
        raw_target = match.group(1).strip()
        if not raw_target or raw_target.startswith("#"):
            continue
        if "://" in raw_target or raw_target.startswith("mailto:"):
            continue
        target = raw_target.split("#", maxsplit=1)[0]
        links.append(target)
    return links


def test_unsuffixed_blueprint_docs_are_english_only() -> None:
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in _iter_unsuffixed_blueprint_docs()
        if HANGUL_PATTERN.search(path.read_text())
    ]

    assert offenders == []


def test_unsuffixed_blueprint_docs_have_korean_mirrors() -> None:
    missing = [
        str(path.relative_to(REPO_ROOT))
        for path in _iter_unsuffixed_blueprint_docs()
        if not _ko_mirror_for(path).exists()
    ]

    assert missing == []


def test_blueprint_markdown_links_resolve_to_local_files() -> None:
    missing_targets: list[str] = []

    for path in sorted(BLUEPRINT_ROOT.rglob("*.md")):
        for target in _iter_local_markdown_links(path):
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                missing_targets.append(f"{path.relative_to(REPO_ROOT)} -> {target}")

    assert missing_targets == []

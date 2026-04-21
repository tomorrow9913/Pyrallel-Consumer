from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HANGUL_PATTERN = re.compile(r"[가-힣]")
MARKDOWN_LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
POLICY_DOC = REPO_ROOT / "docs" / "internal-doc-language-policy.md"
OPERATIONS_POLICY_DOC = REPO_ROOT / "docs" / "operations" / "language-policy.md"
UNSUFFIXED_DOCS = [
    REPO_ROOT / "prd.md",
    REPO_ROOT / "prd_dev.md",
]
KOREAN_MIRRORS = [
    REPO_ROOT / "prd.ko.md",
    REPO_ROOT / "prd_dev.ko.md",
]
LINK_CHECK_DOCS = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "README.ko.md",
    REPO_ROOT / "docs" / "index.md",
    REPO_ROOT / "docs" / "operations" / "index.md",
    POLICY_DOC,
    OPERATIONS_POLICY_DOC,
    *UNSUFFIXED_DOCS,
    *KOREAN_MIRRORS,
]
POLICY_LINK_SURFACES = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "README.ko.md",
    REPO_ROOT / "docs" / "index.md",
    REPO_ROOT / "docs" / "operations" / "index.md",
]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="strict")


def _iter_local_markdown_links(path: Path) -> list[str]:
    text = _read_text(path)
    links: list[str] = []
    for match in MARKDOWN_LINK_PATTERN.finditer(text):
        raw_target = match.group(1).strip()
        if not raw_target or raw_target.startswith("#"):
            continue
        if "://" in raw_target or raw_target.startswith("mailto:"):
            continue
        links.append(raw_target.split("#", maxsplit=1)[0])
    return links


def test_unsuffixed_internal_docs_are_english_only() -> None:
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in UNSUFFIXED_DOCS
        if HANGUL_PATTERN.search(_read_text(path))
    ]

    assert offenders == []


def test_internal_prd_docs_have_korean_mirrors() -> None:
    missing = [
        str(path.relative_to(REPO_ROOT)) for path in KOREAN_MIRRORS if not path.exists()
    ]

    assert missing == []


def test_internal_doc_language_policy_lists_rule_and_exemptions() -> None:
    text = _read_text(POLICY_DOC)
    operations_text = _read_text(OPERATIONS_POLICY_DOC)

    assert "unsuffixed `.md` files are the English canonical documents" in text
    assert "Korean mirrors must use the sibling `*.ko.md` filename" in text
    assert "`GEMINI.md`" in text
    assert "`docs/plans/**`" in text
    assert "`prd.md`" in text
    assert "`prd_dev.md`" in text
    assert (
        "unsuffixed `.md` files are the English canonical documents" in operations_text
    )
    assert "Korean mirrors must use the sibling `*.ko.md` filename" in operations_text
    assert "`GEMINI.md`" in operations_text
    assert "`docs/plans/**`" in operations_text


def test_internal_doc_language_policy_is_linked_from_readmes_and_docs() -> None:
    for path in POLICY_LINK_SURFACES:
        text = _read_text(path)
        assert "internal-doc-language-policy.md" in text
        assert "language-policy.md" in text


def test_internal_doc_language_links_resolve() -> None:
    missing_targets: list[str] = []

    for path in LINK_CHECK_DOCS:
        for target in _iter_local_markdown_links(path):
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                missing_targets.append(f"{path.relative_to(REPO_ROOT)} -> {target}")

    assert missing_targets == []

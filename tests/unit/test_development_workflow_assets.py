from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DOC = (
    REPO_ROOT / "docs" / "operations" / "development-verification-workflow.md"
)


def test_development_verification_workflow_is_linked_from_doc_indexes() -> None:
    docs_index = (REPO_ROOT / "docs" / "index.md").read_text()
    operations_index = (REPO_ROOT / "docs" / "operations" / "index.md").read_text()

    assert WORKFLOW_DOC.exists()
    assert "operations/development-verification-workflow.md" in docs_index
    assert "development-verification-workflow.md" in operations_index


def test_development_verification_workflow_lists_canonical_commands() -> None:
    text = WORKFLOW_DOC.read_text()

    required_commands = [
        "uv sync",
        "uv sync --group dev",
        "uv run python -V",
        ".venv/bin/python -V",
        "UV_CACHE_DIR=.uv-cache uv run pytest tests/unit -q --ignore=tests/unit/benchmarks",
        "UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/benchmarks -q",
        "UV_CACHE_DIR=.uv-cache uv run pytest tests/integration -q",
        "UV_CACHE_DIR=.uv-cache PYRALLEL_E2E_REQUIRE_BROKER=1 uv run pytest tests/e2e -q",
        "UV_CACHE_DIR=.uv-cache uv run ruff check .",
        "UV_CACHE_DIR=.uv-cache uv run ruff format --check .",
        "UV_CACHE_DIR=.uv-cache uv run mypy pyrallel_consumer",
        "UV_CACHE_DIR=.uv-cache uv run bandit -q -lll -r pyrallel_consumer",
        "UV_CACHE_DIR=.uv-cache uv build",
        "UV_CACHE_DIR=.uv-cache uv run twine check dist/*",
        "pre-commit run --all-files",
    ]

    for command in required_commands:
        assert command in text

    assert "No Makefile, justfile, or task runner is currently tracked" in text
    assert "requirements.txt and dev-requirements.txt are not tracked" in text
    assert "`uv sync` creates the project `.venv`" in text
    assert "Do not rely on the system Python for project verification" in text


def test_development_verification_workflow_defines_parallel_worktree_rules() -> None:
    text = WORKFLOW_DOC.read_text()

    required_phrases = [
        "Use separate git worktrees for local parallel work",
        "Do not run parallel agents in the same worktree",
        "git worktree add",
        "git worktree list",
        "git worktree remove",
        "git worktree prune",
        "owner, issue id, branch name, base ref, and cleanup decision",
        "Run `uv sync --group dev` inside each worktree",
        "Never remove a worktree with uncommitted work",
    ]

    for phrase in required_phrases:
        assert phrase in text

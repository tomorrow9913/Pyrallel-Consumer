from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_DOC = (
    REPO_ROOT / "docs" / "operations" / "development-verification-workflow.md"
)
README_DOC = REPO_ROOT / "README.md"
README_KO_DOC = REPO_ROOT / "README.ko.md"


def test_development_verification_workflow_is_linked_from_doc_indexes() -> None:
    # Given: inputs for `development verification workflow is linked f...` are prepared.
    docs_index = (REPO_ROOT / "docs" / "index.md").read_text()
    operations_index = (REPO_ROOT / "docs" / "operations" / "index.md").read_text()

    # When: the development workflow documentation asset code path is exercised.
    # Then: the expected `development verification workflow is linked f...` behavior is asserted.
    assert WORKFLOW_DOC.exists()
    assert "operations/development-verification-workflow.md" in docs_index
    assert "development-verification-workflow.md" in operations_index


def test_development_verification_workflow_lists_canonical_commands() -> None:
    # Given: inputs for `development verification workflow lists canon...` are prepared.
    text = WORKFLOW_DOC.read_text()

    required_commands = [
        "uv sync",
        "uv sync --group dev",
        "uv run python -V",
        ".venv/bin/python -V",
        "UV_CACHE_DIR=.uv-cache uv run pytest tests/unit -q",
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

    # When: the development workflow documentation asset code path is exercised.
    for command in required_commands:
        assert command in text

    # Then: the expected `development verification workflow lists canon...` behavior is asserted.
    assert "No Makefile, justfile, or task runner is currently tracked" in text
    assert "requirements.txt and dev-requirements.txt are not tracked" in text
    assert "`uv sync` creates the project `.venv`" in text
    assert "Do not rely on the system Python for project verification" in text


def test_development_verification_workflow_defines_parallel_worktree_rules() -> None:
    # Given: inputs for `development verification workflow defines par...` are prepared.
    # When: the development workflow documentation asset code path is exercised.
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

    # Then: the expected `development verification workflow defines par...` behavior is asserted.
    for phrase in required_phrases:
        assert phrase in text


def test_readmes_document_required_broker_e2e_gate() -> None:
    # Given: local README files document default and release-gate E2E behavior.
    english_readme = README_DOC.read_text(encoding="utf-8")
    korean_readme = README_KO_DOC.read_text(encoding="utf-8")

    required_terms = [
        "PYRALLEL_E2E_REQUIRE_BROKER=1 uv run pytest tests/e2e -q",
        "skip",
        "fail",
    ]

    # Then: both public READMEs expose the required-broker fail-closed command.
    for document in (english_readme, korean_readme):
        for term in required_terms:
            assert term in document

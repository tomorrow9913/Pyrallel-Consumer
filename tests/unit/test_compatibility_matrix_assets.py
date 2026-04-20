from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_compatibility_manifest_covers_supported_python_and_client_rows() -> None:
    manifest = json.loads(
        (REPO_ROOT / ".github" / "compatibility-matrix.json").read_text()
    )
    rows = manifest["include"]

    row_signatures = {
        (
            row["python-version"],
            row["client-version"],
            row["kafka-image"],
            row["test-target"],
        )
        for row in rows
    }

    assert (
        "3.12",
        "2.13.0",
        "confluentinc/cp-kafka:7.6.0",
        "tests/e2e/test_ordering.py::test_key_hash_ordering",
    ) in row_signatures
    assert (
        "3.13",
        "locked",
        "confluentinc/cp-kafka:7.6.0",
        "tests/e2e/test_ordering.py::test_key_hash_ordering",
    ) in row_signatures


def test_compatibility_workflow_uses_manifest_and_generated_docs() -> None:
    workflow_text = (
        REPO_ROOT / ".github" / "workflows" / "compatibility-matrix.yml"
    ).read_text()

    assert ".github/compatibility-matrix.json" in workflow_text
    assert "fromJSON(needs.plan.outputs.matrix)" in workflow_text
    assert "scripts/compatibility_matrix.py --check" in workflow_text
    assert "KAFKA_IMAGE: ${{ matrix.kafka-image }}" in workflow_text
    assert 'uv run pytest "${{ matrix.test-target }}" -q --maxfail=1' in workflow_text
    assert "docs/operations/compatibility-matrix.md" in workflow_text


def test_release_verify_checks_compatibility_matrix_drift() -> None:
    release_workflow = (
        REPO_ROOT / ".github" / "workflows" / "release-verify.yml"
    ).read_text()

    assert ".github/compatibility-matrix.json" in release_workflow
    assert "scripts/compatibility_matrix.py" in release_workflow
    assert ".github/workflows/compatibility-matrix.yml" in release_workflow
    assert "scripts/compatibility_matrix.py --check" in release_workflow


def test_operations_docs_publish_verified_compatibility_rows() -> None:
    compatibility_doc = (
        REPO_ROOT / "docs" / "operations" / "compatibility-matrix.md"
    ).read_text()
    docs_index = (REPO_ROOT / "docs" / "index.md").read_text()
    operations_index = (REPO_ROOT / "docs" / "operations" / "index.md").read_text()
    support_policy = (
        REPO_ROOT / "docs" / "operations" / "support-policy.md"
    ).read_text()
    readme = (REPO_ROOT / "README.md").read_text()
    readme_ko = (REPO_ROOT / "README.ko.md").read_text()

    assert (
        "| Python | Kafka baseline | Client | Verification | Workflow | Notes |"
        in compatibility_doc
    )
    assert "compatibility-matrix.yml" in compatibility_doc
    assert "confluentinc/cp-kafka:7.6.0" in compatibility_doc
    assert (
        "| 3.12 | confluentinc/cp-kafka:7.6.0 | floor client 2.13.0 |"
        in compatibility_doc
    )
    assert "| 3.13 | confluentinc/cp-kafka:7.6.0 | locked client |" in compatibility_doc
    assert "release-verify.yml" in compatibility_doc
    assert "scripts/compatibility_matrix.py --check" in compatibility_doc
    assert "operations/compatibility-matrix.md" in docs_index
    assert "compatibility-matrix.md" in operations_index
    assert "compatibility-matrix.md" in support_policy
    assert "compatibility-matrix.md" in readme
    assert "compatibility-matrix.md" in readme_ko


def test_generator_check_accepts_tracked_markdown() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/compatibility_matrix.py", "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert "compatibility-matrix.md is up to date" in result.stdout

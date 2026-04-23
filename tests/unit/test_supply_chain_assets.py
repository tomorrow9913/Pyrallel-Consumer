from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
RELEASE_VERIFY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-verify.yml"
PUBLISH_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish-pypi.yml"
DEPENDABOT_CONFIG = REPO_ROOT / ".github" / "dependabot.yml"


def test_ci_release_verify_and_publish_run_lockfile_sca_gate() -> None:
    for workflow_path in (CI_WORKFLOW, RELEASE_VERIFY_WORKFLOW, PUBLISH_WORKFLOW):
        text = workflow_path.read_text(encoding="utf-8")

        assert "uv export --frozen --format requirements.txt" in text
        assert "--all-groups" in text
        assert "--no-hashes" in text
        assert "--no-emit-project" in text
        assert "uvx --from pip-audit pip-audit" in text
        assert "--no-deps" in text
        assert "--disable-pip" in text
        assert ".artifacts/locked-requirements.txt" in text


def test_publish_workflow_attests_built_distribution_artifacts() -> None:
    text = PUBLISH_WORKFLOW.read_text(encoding="utf-8")
    build_job = text.split("\n  publish:", maxsplit=1)[0]
    publish_job = text.split("\n  publish:", maxsplit=1)[1]

    assert "attestations: write" in build_job
    assert "id-token: write" in build_job
    assert "actions/attest-build-provenance@v4.1.0" in build_job
    assert "subject-path:" in build_job
    assert "${{ steps.release_artifacts.outputs.sdist_path }}" in build_job
    assert "${{ steps.release_artifacts.outputs.wheel_path }}" in build_job
    assert "id-token: write" in publish_job
    assert "pypa/gh-action-pypi-publish@release/v1" in publish_job


def test_release_verify_attests_built_distribution_artifacts() -> None:
    text = RELEASE_VERIFY_WORKFLOW.read_text(encoding="utf-8")

    assert "id-token: write" in text
    assert "attestations: write" in text
    assert "actions/attest-build-provenance@v4.1.0" in text
    assert "subject-path:" in text
    assert "${{ steps.release_artifacts.outputs.sdist_path }}" in text
    assert "${{ steps.release_artifacts.outputs.wheel_path }}" in text


def test_dependabot_tracks_uv_and_github_actions_ecosystems() -> None:
    text = DEPENDABOT_CONFIG.read_text(encoding="utf-8")

    assert 'package-ecosystem: "uv"' in text
    assert 'package-ecosystem: "github-actions"' in text
    assert 'directory: "/"' in text
    assert 'target-branch: "develop"' in text
    assert 'interval: "weekly"' in text

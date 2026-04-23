from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml  # type: ignore

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
RELEASE_VERIFY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-verify.yml"
PUBLISH_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish-pypi.yml"
DEPENDABOT_CONFIG = REPO_ROOT / ".github" / "dependabot.yml"


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _job_has_step_command(job: dict[str, Any], command: str) -> bool:
    for step in job.get("steps", []):
        run = step.get("run")
        if isinstance(run, str) and command in run:
            return True
    return False


def _assert_sca_gate_steps(ci_jobs: dict[str, Any], job_name: str) -> None:
    job = ci_jobs[job_name]

    assert _job_has_step_command(
        job,
        "uv export --frozen --format requirements.txt --all-groups --no-hashes --no-emit-project",
    )
    assert _job_has_step_command(
        job,
        "uv run pip-audit --no-deps --disable-pip -r .artifacts/locked-requirements.txt",
    )


def _workflow_triggers(text: dict[str, Any]) -> dict[str, Any]:
    if "on" in text:
        return cast(dict[str, Any], text["on"])
    if True in text:
        return cast(dict[str, Any], text[True])  # type: ignore[index]
    raise KeyError("Workflow trigger section not found")


def _normalized_subject_lines(subject_text: str) -> list[str]:
    return [line.strip() for line in subject_text.splitlines() if line.strip()]


def test_ci_release_verify_and_publish_quality_jobs_run_lockfile_sca_gate() -> None:
    ci = _load_yaml(CI_WORKFLOW)
    _assert_sca_gate_steps(ci["jobs"], "quality")

    release = _load_yaml(RELEASE_VERIFY_WORKFLOW)
    _assert_sca_gate_steps(release["jobs"], "verify")

    publish = _load_yaml(PUBLISH_WORKFLOW)
    _assert_sca_gate_steps(publish["jobs"], "build")


def test_publish_workflow_attests_built_distribution_artifacts() -> None:
    text = _load_yaml(PUBLISH_WORKFLOW)
    jobs = text["jobs"]
    build_job = jobs["build"]
    publish_job = jobs["publish"]

    assert build_job["permissions"]["attestations"] == "write"
    assert build_job["permissions"]["id-token"] == "write"
    assert publish_job["permissions"]["id-token"] == "write"
    assert any(
        step.get("uses") == "actions/attest-build-provenance@v4.1.0"
        for step in build_job["steps"]
    )
    attest_step = next(
        (
            step
            for step in build_job["steps"]
            if isinstance(step, dict)
            and step.get("uses") == "actions/attest-build-provenance@v4.1.0"
            and isinstance(step.get("with"), dict)
            and "${{ steps.release_artifacts.outputs.sdist_path }}"
            in step["with"].get("subject-path", "")
        ),
        None,
    )
    assert attest_step is not None
    subject_lines = _normalized_subject_lines(
        attest_step["with"]["subject-path"]  # type: ignore[index]
    )
    assert subject_lines == [
        "${{ steps.release_artifacts.outputs.sdist_path }}",
        "${{ steps.release_artifacts.outputs.wheel_path }}",
    ]
    assert any(
        step.get("uses") == "pypa/gh-action-pypi-publish@release/v1"
        for step in publish_job["steps"]
    )


def test_release_verify_attests_built_distribution_artifacts() -> None:
    text = _load_yaml(RELEASE_VERIFY_WORKFLOW)
    jobs = text["jobs"]
    verify_job = jobs["verify"]

    assert text["permissions"]["id-token"] == "write"
    assert text["permissions"]["attestations"] == "write"
    attest_step = next(
        (
            step
            for step in verify_job["steps"]
            if isinstance(step, dict)
            and step.get("uses") == "actions/attest-build-provenance@v4.1.0"
            and isinstance(step.get("with"), dict)
            and "${{ steps.release_artifacts.outputs.sdist_path }}"
            in step["with"].get("subject-path", "")
        ),
        None,
    )
    assert attest_step is not None
    subject_lines = _normalized_subject_lines(
        attest_step["with"]["subject-path"]  # type: ignore[index]
    )
    assert subject_lines == [
        "${{ steps.release_artifacts.outputs.sdist_path }}",
        "${{ steps.release_artifacts.outputs.wheel_path }}",
    ]


def test_release_verify_triggers_on_supply_chain_controls() -> None:
    text = _load_yaml(RELEASE_VERIFY_WORKFLOW)
    triggers = _workflow_triggers(text)
    paths = triggers["push"]["paths"]
    for expected in (
        ".github/workflows/publish-pypi.yml",
        ".github/dependabot.yml",
        "tests/unit/test_supply_chain_assets.py",
    ):
        assert expected in paths


def test_ci_triggers_on_supply_chain_controls() -> None:
    text = _load_yaml(CI_WORKFLOW)
    triggers = _workflow_triggers(text)
    paths = triggers["push"]["paths"] + triggers["pull_request"]["paths"]
    for expected in (
        ".github/workflows/publish-pypi.yml",
        ".github/workflows/release-verify.yml",
        ".github/dependabot.yml",
        "tests/unit/test_supply_chain_assets.py",
    ):
        assert expected in paths


def test_dependabot_tracks_uv_and_github_actions_ecosystems() -> None:
    text = _load_yaml(DEPENDABOT_CONFIG)
    updates = text["updates"]  # type: ignore[assignment]

    assert isinstance(updates, list)

    uv_entry = next(
        (entry for entry in updates if entry.get("package-ecosystem") == "uv"), None
    )
    actions_entry = next(
        (
            entry
            for entry in updates
            if entry.get("package-ecosystem") == "github-actions"
        ),
        None,
    )
    assert uv_entry is not None
    assert actions_entry is not None

    for entry in (uv_entry, actions_entry):
        assert entry.get("directory") == "/"
        assert entry.get("target-branch") == "develop"
        assert isinstance(entry.get("schedule"), dict)
        assert entry["schedule"].get("interval") == "weekly"  # type: ignore[index]

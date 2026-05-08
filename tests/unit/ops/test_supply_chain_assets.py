from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml  # type: ignore

REPO_ROOT = Path(__file__).resolve().parents[3]
UNIT_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "unit.yml"
RELEASE_VERIFY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-verify.yml"
PUBLISH_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish-pypi.yml"
DEPENDABOT_CONFIG = REPO_ROOT / ".github" / "dependabot.yml"
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"


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


def test_unit_release_verify_and_publish_quality_jobs_run_lockfile_sca_gate() -> None:
    # Given: unit, release-verify, and publish workflows are available as YAML.
    unit = _load_yaml(UNIT_WORKFLOW)
    _assert_sca_gate_steps(unit["jobs"], "quality")

    release = _load_yaml(RELEASE_VERIFY_WORKFLOW)
    _assert_sca_gate_steps(release["jobs"], "verify")

    publish = _load_yaml(PUBLISH_WORKFLOW)
    # When: each quality job is inspected for lockfile supply-chain audit commands.
    # Then: all quality jobs include export and pip-audit SCA gate steps.
    _assert_sca_gate_steps(publish["jobs"], "build")


def test_publish_workflow_attests_built_distribution_artifacts() -> None:
    # Given: the publish workflow build and publish jobs are loaded from YAML.
    text = _load_yaml(PUBLISH_WORKFLOW)
    jobs = text["jobs"]
    build_job = jobs["build"]
    publish_job = jobs["publish"]

    # When: artifact attestation and PyPI publish settings are inspected.
    # Then: attestation permissions, subject paths, and trusted publishing are configured.
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
            and "subject-path" in step["with"]
        ),
        None,
    )
    assert attest_step is not None
    attest_with = attest_step.get("with")
    assert isinstance(attest_with, dict)
    subject_path = attest_with.get("subject-path")
    assert isinstance(subject_path, str)
    subject_lines = _normalized_subject_lines(subject_path)
    assert subject_lines == [
        "${{ steps.release_artifacts.outputs.sdist_path }}",
        "${{ steps.release_artifacts.outputs.wheel_path }}",
    ]
    assert any(
        step.get("uses") == "pypa/gh-action-pypi-publish@release/v1"
        for step in publish_job["steps"]
    )


def test_release_verify_attests_built_distribution_artifacts() -> None:
    # Given: the release-verify workflow verify job is loaded from YAML.
    text = _load_yaml(RELEASE_VERIFY_WORKFLOW)
    jobs = text["jobs"]
    verify_job = jobs["verify"]

    # When: artifact attestation settings are inspected.
    # Then: attestation permissions and built artifact subject paths are configured.
    assert text["permissions"]["id-token"] == "write"
    assert text["permissions"]["attestations"] == "write"
    attest_step = next(
        (
            step
            for step in verify_job["steps"]
            if isinstance(step, dict)
            and step.get("uses") == "actions/attest-build-provenance@v4.1.0"
            and isinstance(step.get("with"), dict)
            and "subject-path" in step["with"]
        ),
        None,
    )
    assert attest_step is not None
    attest_with = attest_step.get("with")
    assert isinstance(attest_with, dict)
    subject_path = attest_with.get("subject-path")
    assert isinstance(subject_path, str)
    subject_lines = _normalized_subject_lines(subject_path)
    assert subject_lines == [
        "${{ steps.release_artifacts.outputs.sdist_path }}",
        "${{ steps.release_artifacts.outputs.wheel_path }}",
    ]


def test_release_verify_triggers_on_supply_chain_controls() -> None:
    # Given: the release-verify workflow trigger paths are loaded from YAML.
    text = _load_yaml(RELEASE_VERIFY_WORKFLOW)
    triggers = _workflow_triggers(text)
    paths = triggers["push"]["paths"]
    # When: push path filters are inspected for supply-chain control files.
    # Then: workflow, Dependabot, and ops supply-chain tests trigger release verification.
    for expected in (
        ".github/workflows/publish-pypi.yml",
        ".github/dependabot.yml",
        "tests/unit/ops/test_supply_chain_assets.py",
    ):
        assert expected in paths


def test_unit_triggers_on_supply_chain_controls() -> None:
    # Given: the unit workflow push and pull-request path filters are loaded.
    text = _load_yaml(UNIT_WORKFLOW)
    triggers = _workflow_triggers(text)
    paths = triggers["push"]["paths"] + triggers["pull_request"]["paths"]
    # When: the combined trigger path filters are inspected.
    # Then: changes under .github and tests/unit are included in unit validation.
    for expected in (
        ".github/**",
        "tests/unit/**",
    ):
        assert expected in paths


def test_pr_facing_validation_workflows_are_unit_integration_and_e2e() -> None:
    # Given: all GitHub workflow files are available for trigger inspection.
    pr_workflows = set()
    for workflow_path in WORKFLOW_DIR.glob("*.yml"):
        text = _load_yaml(workflow_path)
        triggers = _workflow_triggers(text)
        if "pull_request" in triggers:
            pr_workflows.add(workflow_path.name)

    # When: workflows with pull_request triggers are collected.
    # Then: only unit, integration, and e2e workflows face pull requests.
    assert pr_workflows == {"unit.yml", "integration.yml", "e2e.yml"}


def test_validation_workflows_avoid_duplicate_develop_push_checks() -> None:
    # Given: unit, integration, and e2e workflow trigger definitions are loaded.
    # When: push and pull-request branch filters are inspected.
    # Then: push checks run on main while PR checks cover main and develop.
    for workflow_name in ("unit.yml", "integration.yml", "e2e.yml"):
        text = _load_yaml(WORKFLOW_DIR / workflow_name)
        triggers = _workflow_triggers(text)

        assert triggers["push"]["branches"] == ["main"]
        assert triggers["pull_request"]["branches"] == ["main", "develop"]


def test_dependabot_tracks_uv_and_github_actions_ecosystems() -> None:
    # Given: the Dependabot configuration is loaded from YAML.
    text = _load_yaml(DEPENDABOT_CONFIG)
    updates = text["updates"]

    # When: uv and GitHub Actions update entries are inspected.
    # Then: both ecosystems share the weekly develop-targeted dependency group.
    assert isinstance(updates, list)

    uv_entry = next(
        (
            entry
            for entry in updates
            if isinstance(entry, dict) and entry.get("package-ecosystem") == "uv"
        ),
        None,
    )
    actions_entry = next(
        (
            entry
            for entry in updates
            if isinstance(entry, dict)
            and entry.get("package-ecosystem") == "github-actions"
        ),
        None,
    )
    assert uv_entry is not None
    assert actions_entry is not None
    assert isinstance(uv_entry, dict)
    assert isinstance(actions_entry, dict)

    groups = text["multi-ecosystem-groups"]
    assert isinstance(groups, dict)
    weekly_group = groups["weekly-dependencies"]
    assert isinstance(weekly_group, dict)
    assert weekly_group.get("target-branch") == "develop"
    assert isinstance(weekly_group.get("schedule"), dict)
    assert weekly_group["schedule"].get("interval") == "weekly"  # type: ignore[index]

    for entry in (uv_entry, actions_entry):
        assert entry.get("directory") == "/"
        assert entry.get("multi-ecosystem-group") == "weekly-dependencies"

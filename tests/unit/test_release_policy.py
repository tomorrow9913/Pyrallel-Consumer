from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

release_policy = importlib.import_module("scripts.release_policy")
PUBLISH_WORKFLOW = ROOT / ".github" / "workflows" / "publish-pypi.yml"
RELEASE_VERIFY_WORKFLOW = ROOT / ".github" / "workflows" / "release-verify.yml"


def test_classify_branch_kinds() -> None:
    assert release_policy.classify_branch("develop") == "develop"
    assert release_policy.classify_branch("release/0.3") == "release"
    assert release_policy.classify_branch("main") == "main"
    assert release_policy.classify_branch("hotfix/0.3.1") == "hotfix"


@pytest.mark.parametrize(
    ("branch", "version", "expected"),
    [
        ("develop", "0.3.0a8", True),
        ("develop", "0.3.0b1", False),
        ("release/0.3", "0.3.0b2", True),
        ("release/0.3", "0.3.0rc1", True),
        ("release/0.3", "0.3.0a9", False),
        ("main", "0.3.0", True),
        ("main", "0.3.0rc2", False),
        ("hotfix/0.3.1", "0.3.2", True),
        ("hotfix/0.3.1", "0.3.2rc1", False),
    ],
)
def test_validate_branch_version(branch: str, version: str, expected: bool) -> None:
    assert release_policy.validate_branch_version(branch, version) is expected


def test_validate_tag_version_exact_match() -> None:
    assert release_policy.validate_tag_version("v0.3.0rc1", "0.3.0rc1") is True
    assert release_policy.validate_tag_version("v0.3.0-rc.1", "0.3.0rc1") is False


@pytest.mark.parametrize(
    ("base_branch", "head_branch", "expected"),
    [
        ("main", "release/0.3", True),
        ("main", "hotfix/0.3.1", True),
        ("main", "feat/parallel-fix", False),
        ("main", "develop", False),
        ("develop", "feat/parallel-fix", True),
        ("release/0.3", "feat/release-fix", True),
    ],
)
def test_validate_pull_request_flow(
    base_branch: str, head_branch: str, expected: bool
) -> None:
    assert (
        release_policy.validate_pull_request_flow(base_branch, head_branch) is expected
    )


def test_validate_pull_request_flow_unsupported_base_branch() -> None:
    with pytest.raises(release_policy.PolicyError, match="unsupported branch"):
        release_policy.validate_pull_request_flow(
            "feature/not-supported", "feat/example"
        )


def test_resolve_release_artifacts_success(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    sdist = dist_dir / "pyrallel_consumer-0.3.0rc1.tar.gz"
    wheel = dist_dir / "pyrallel_consumer-0.3.0rc1-py3-none-any.whl"
    sdist.write_text("sdist")
    wheel.write_text("wheel")

    resolved_sdist, resolved_wheel = release_policy.resolve_release_artifacts(
        str(dist_dir), "pyrallel-consumer", "0.3.0rc1"
    )

    assert resolved_sdist == str(sdist)
    assert resolved_wheel == str(wheel)


def test_resolve_release_artifacts_rejects_stale_dist_files(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "pyrallel_consumer-0.3.0rc1.tar.gz").write_text("sdist")
    (dist_dir / "pyrallel_consumer-0.3.0rc1-py3-none-any.whl").write_text("wheel")
    stale = dist_dir / "pyrallel_consumer-0.2.9-py3-none-any.whl"
    stale.write_text("stale-wheel")

    with pytest.raises(
        release_policy.PolicyError, match="stale distribution artifacts"
    ):
        release_policy.resolve_release_artifacts(
            str(dist_dir), "pyrallel-consumer", "0.3.0rc1"
        )


@pytest.mark.parametrize(
    ("func_name", "version", "expected"),
    [
        ("bump_alpha", "0.3.0a8", "0.3.0a9"),
        ("bump_beta", "0.3.0b2", "0.3.0b3"),
        ("bump_rc", "0.3.0rc1", "0.3.0rc2"),
        ("bump_patch", "0.3.1", "0.3.2"),
    ],
)
def test_bump_helpers(func_name: str, version: str, expected: str) -> None:
    func = getattr(release_policy, func_name)
    assert func(version) == expected


def test_cli_validate_branch_version_success() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "release_policy.py"),
            "validate-branch-version",
            "--branch",
            "develop",
            "--version",
            "0.3.0a8",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "OK" in result.stdout


def test_cli_validate_branch_version_failure() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "release_policy.py"),
            "validate-branch-version",
            "--branch",
            "main",
            "--version",
            "0.3.0rc2",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "INVALID" in result.stdout


def test_cli_validate_tag_version() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "release_policy.py"),
            "validate-tag-version",
            "--tag",
            "v0.3.0rc1",
            "--version",
            "0.3.0rc1",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "OK" in result.stdout


def test_cli_validate_pr_flow_success() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "release_policy.py"),
            "validate-pr-flow",
            "--base-branch",
            "main",
            "--head-branch",
            "release/0.3",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "OK" in result.stdout


def test_cli_validate_pr_flow_failure() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "release_policy.py"),
            "validate-pr-flow",
            "--base-branch",
            "main",
            "--head-branch",
            "feat/direct-main-pr",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "INVALID" in result.stdout


def test_cli_resolve_artifacts(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        "\n".join(
            [
                "[project]",
                'name = "pyrallel-consumer"',
                'version = "0.3.0rc1"',
                "",
            ]
        )
    )
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    sdist = dist_dir / "pyrallel_consumer-0.3.0rc1.tar.gz"
    wheel = dist_dir / "pyrallel_consumer-0.3.0rc1-py3-none-any.whl"
    sdist.write_text("sdist")
    wheel.write_text("wheel")

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "release_policy.py"),
            "resolve-artifacts",
            "--dist-dir",
            str(dist_dir),
            "--project-file",
            str(pyproject),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    assert lines == [str(sdist), str(wheel)]


def test_publish_workflow_uses_release_policy_and_trusted_publishing() -> None:
    text = PUBLISH_WORKFLOW.read_text()

    assert "workflow_dispatch:" in text
    assert "scripts.release_policy" in text
    assert "validate_branch_version" in text
    assert "validate_tag_version" in text
    assert "resolve-artifacts --write-github-output" in text
    assert "id-token: write" in text
    assert "pypa/gh-action-pypi-publish@release/v1" in text


def test_publish_workflow_validates_branch_and_tag_refs_separately() -> None:
    text = PUBLISH_WORKFLOW.read_text()

    assert "if: ${{ github.ref_type == 'branch' }}" in text
    assert "if: ${{ github.ref_type == 'tag' }}" in text


def test_release_verify_runs_policy_preflight_and_smoke_install() -> None:
    text = RELEASE_VERIFY_WORKFLOW.read_text()

    assert "scripts.release_policy" in text
    assert "validate_branch_version" in text
    assert "validate_tag_version" in text
    assert "resolve-artifacts --write-github-output" in text
    assert "Smoke install/import from built wheel" in text
    assert "pip install dist/pyrallel_consumer-*.whl" in text

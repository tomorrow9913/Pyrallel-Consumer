from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

release_policy = importlib.import_module("scripts.release_policy")
PUBLISH_WORKFLOW = ROOT / ".github" / "workflows" / "publish-pypi.yml"
RELEASE_VERIFY_WORKFLOW = ROOT / ".github" / "workflows" / "release-verify.yml"


def test_classify_branch_kinds() -> None:
    # Given: develop, release, main, and hotfix branch names are provided.
    # When: classify_branch categorizes each release workflow branch.
    # Then: each branch maps to its expected release policy kind.
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
    # Given: branch, version, and expected validity parameters are provided.
    # When: validate_branch_version evaluates the branch/version pair.
    # Then: the result matches the policy expectation for that pair.
    assert release_policy.validate_branch_version(branch, version) is expected


def test_validate_branch_version_does_not_use_optimized_assert_guards() -> None:
    # Given: the release policy source file is available for static inspection.
    source = (ROOT / "scripts" / "release_policy.py").read_text(encoding="utf-8")

    # When: the source text is read from scripts/release_policy.py.
    # Then: the validation path does not rely on assert statements removable by -O.
    assert "assert match is not None" not in source


def test_validate_tag_version_exact_match() -> None:
    # Given: PEP-style and dashed release tag forms are provided.
    # When: validate_tag_version compares each tag against the project version.
    # Then: only the exact v-prefixed version tag is accepted.
    assert release_policy.validate_tag_version("v0.3.0rc1", "0.3.0rc1") is True
    assert release_policy.validate_tag_version("v0.3.0-rc.1", "0.3.0rc1") is False


def test_latest_concrete_changelog_heading_skips_unreleased() -> None:
    # Given: a changelog contains Unreleased followed by version 1.2.3.
    changelog = "\n".join(
        [
            "# Changelog",
            "",
            "## [Unreleased]",
            "",
            "## [1.2.3] - 2026-04-22",
            "",
        ]
    )

    # When: latest_concrete_changelog_heading scans the changelog headings.
    # Then: the latest concrete release heading is returned as 1.2.3.
    assert release_policy.latest_concrete_changelog_heading(changelog) == "1.2.3"


def test_latest_concrete_changelog_heading_requires_release_heading() -> None:
    # Given: a changelog contains only an Unreleased heading.
    # When: latest_concrete_changelog_heading scans for a concrete release.
    # Then: a policy error reports the missing concrete release heading.
    with pytest.raises(release_policy.PolicyError, match="concrete release heading"):
        release_policy.latest_concrete_changelog_heading(
            "# Changelog\n\n## [Unreleased]\n"
        )


def test_validate_changelog_version_matches_latest_concrete_heading() -> None:
    # Given: a changelog whose latest concrete heading is 1.2.3 is provided.
    changelog = "# Changelog\n\n## [Unreleased]\n\n## [1.2.3] - 2026-04-22\n"

    # When: validate_changelog_version compares candidate versions to that heading.
    # Then: version 1.2.3 is accepted and version 1.2.4 is rejected.
    assert release_policy.validate_changelog_version(changelog, "1.2.3") is True
    assert release_policy.validate_changelog_version(changelog, "1.2.4") is False


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
    # Given: base branch, head branch, and expected validity parameters are provided.
    # When: validate_pull_request_flow checks the proposed PR branch route.
    # Then: the route is accepted only when it matches the release policy.
    assert (
        release_policy.validate_pull_request_flow(base_branch, head_branch) is expected
    )


def test_validate_pull_request_flow_unsupported_base_branch() -> None:
    # Given: an unsupported feature base branch and a feature head branch are provided.
    # When: validate_pull_request_flow evaluates the unsupported base route.
    # Then: a policy error reports the unsupported target branch.
    with pytest.raises(release_policy.PolicyError, match="unsupported branch"):
        release_policy.validate_pull_request_flow(
            "feature/not-supported", "feat/example"
        )


def test_resolve_release_artifacts_success(tmp_path: Path) -> None:
    # Given: matching sdist and wheel artifacts for pyrallel-consumer 0.3.0rc1 exist.
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    sdist = dist_dir / "pyrallel_consumer-0.3.0rc1.tar.gz"
    wheel = dist_dir / "pyrallel_consumer-0.3.0rc1-py3-none-any.whl"
    sdist.write_text("sdist")
    wheel.write_text("wheel")

    resolved_sdist, resolved_wheel = release_policy.resolve_release_artifacts(
        str(dist_dir), "pyrallel-consumer", "0.3.0rc1"
    )

    # When: resolve_release_artifacts searches the dist directory for that version.
    # Then: the matching sdist and wheel paths are returned.
    assert resolved_sdist == str(sdist)
    assert resolved_wheel == str(wheel)


def test_resolve_release_artifacts_rejects_stale_dist_files(tmp_path: Path) -> None:
    # Given: matching artifacts and a stale 0.2.9 wheel coexist in dist.
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "pyrallel_consumer-0.3.0rc1.tar.gz").write_text("sdist")
    (dist_dir / "pyrallel_consumer-0.3.0rc1-py3-none-any.whl").write_text("wheel")
    stale = dist_dir / "pyrallel_consumer-0.2.9-py3-none-any.whl"
    stale.write_text("stale-wheel")

    # When: resolve_release_artifacts validates the dist contents for 0.3.0rc1.
    # Then: a policy error rejects stale distribution artifacts.
    with pytest.raises(
        release_policy.PolicyError, match="stale distribution artifacts"
    ):
        release_policy.resolve_release_artifacts(
            str(dist_dir), "pyrallel-consumer", "0.3.0rc1"
        )


def _write_release_inputs(
    tmp_path: Path,
    *,
    version: str = "1.2.3",
    changelog_heading: str = "1.2.3",
) -> tuple[Path, Path]:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        "\n".join(
            [
                "[project]",
                'name = "pyrallel-consumer"',
                f'version = "{version}"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "\n".join(
            [
                "# Changelog",
                "",
                "## [Unreleased]",
                "",
                "## [%s] - 2026-04-22" % changelog_heading,
                "",
            ]
        ),
        encoding="utf-8",
    )
    return pyproject, changelog


def test_release_preflight_accepts_matching_branch_tag_version_changelog(
    tmp_path: Path,
) -> None:
    # Given: pyproject and changelog both declare release version 1.2.3.
    pyproject, changelog = _write_release_inputs(tmp_path, version="1.2.3")

    # When: validate_release_preflight checks main branch and v1.2.3 tag refs.
    # Then: both matching refs return the validated release version.
    assert (
        release_policy.validate_release_preflight(
            str(pyproject), str(changelog), ref_name="main", ref_type="branch"
        )
        == "1.2.3"
    )
    assert (
        release_policy.validate_release_preflight(
            str(pyproject), str(changelog), ref_name="v1.2.3", ref_type="tag"
        )
        == "1.2.3"
    )


def test_release_preflight_rejects_stale_changelog_latest_heading(
    tmp_path: Path,
) -> None:
    # Given: pyproject declares 1.2.3 while the latest changelog heading is 1.2.2.
    pyproject, changelog = _write_release_inputs(
        tmp_path, version="1.2.3", changelog_heading="1.2.2"
    )

    # When: validate_release_preflight checks the main branch release inputs.
    # Then: a policy error reports the stale changelog heading.
    with pytest.raises(release_policy.PolicyError, match="CHANGELOG latest heading"):
        release_policy.validate_release_preflight(
            str(pyproject), str(changelog), ref_name="main", ref_type="branch"
        )


def test_release_preflight_rejects_tag_version_mismatch(tmp_path: Path) -> None:
    # Given: release inputs declare 1.2.3 but the tag ref is v1.2.2.
    pyproject, changelog = _write_release_inputs(tmp_path, version="1.2.3")

    # When: validate_release_preflight checks the tag release inputs.
    # Then: a policy error reports the tag/version mismatch.
    with pytest.raises(release_policy.PolicyError, match="Tag/version policy mismatch"):
        release_policy.validate_release_preflight(
            str(pyproject), str(changelog), ref_name="v1.2.2", ref_type="tag"
        )


def test_release_preflight_rejects_branch_version_mismatch(tmp_path: Path) -> None:
    # Given: release inputs declare stable 1.2.3 on the develop branch.
    pyproject, changelog = _write_release_inputs(tmp_path, version="1.2.3")

    # When: validate_release_preflight checks the develop branch inputs.
    # Then: a policy error reports the branch/version mismatch.
    with pytest.raises(
        release_policy.PolicyError, match="Branch/version policy mismatch"
    ):
        release_policy.validate_release_preflight(
            str(pyproject), str(changelog), ref_name="develop", ref_type="branch"
        )


def test_release_preflight_cli_returns_non_zero_for_changelog_mismatch(
    tmp_path: Path,
) -> None:
    # Given: temporary release files contain version 1.2.3 and changelog 1.2.2.
    pyproject, changelog = _write_release_inputs(
        tmp_path, version="1.2.3", changelog_heading="1.2.2"
    )

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "release_policy.py"),
            "release-preflight",
            "--project-file",
            str(pyproject),
            "--changelog-file",
            str(changelog),
            "--ref-name",
            "main",
            "--ref-type",
            "branch",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    # When: the release-preflight CLI validates the mismatched main branch inputs.
    # Then: the CLI exits with code 1 and prints the changelog mismatch.
    assert result.returncode == 1
    assert "CHANGELOG latest heading" in result.stdout


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
    # Given: a bump helper name, source version, and expected next version are provided.
    func = getattr(release_policy, func_name)
    # When: the selected release_policy bump helper is invoked.
    # Then: the returned version matches the expected alpha/beta/rc/patch increment.
    assert func(version) == expected


def test_cli_validate_branch_version_success() -> None:
    # Given: the CLI receives develop and version 0.3.0a8.
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

    # When: validate-branch-version is executed through subprocess.
    # Then: the command exits successfully and prints OK.
    assert result.returncode == 0
    assert "OK" in result.stdout


def test_cli_validate_branch_version_failure() -> None:
    # Given: the CLI receives main and prerelease version 0.3.0rc2.
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

    # When: validate-branch-version is executed through subprocess.
    # Then: the command exits with code 1 and prints INVALID.
    assert result.returncode == 1
    assert "INVALID" in result.stdout


def test_cli_validate_tag_version() -> None:
    # Given: the CLI receives tag v0.3.0rc1 and version 0.3.0rc1.
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

    # When: validate-tag-version is executed through subprocess.
    # Then: the command exits successfully and prints OK.
    assert result.returncode == 0
    assert "OK" in result.stdout


def test_cli_validate_pr_flow_success() -> None:
    # Given: the CLI receives a release/0.3 branch targeting main.
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

    # When: validate-pr-flow is executed through subprocess.
    # Then: the command exits successfully and prints OK.
    assert result.returncode == 0
    assert "OK" in result.stdout


def test_cli_validate_pr_flow_failure() -> None:
    # Given: the CLI receives a feature branch targeting main directly.
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

    # When: validate-pr-flow is executed through subprocess.
    # Then: the command exits with code 1 and prints INVALID.
    assert result.returncode == 1
    assert "INVALID" in result.stdout


def test_cli_resolve_artifacts(tmp_path: Path) -> None:
    # Given: temporary pyproject, sdist, and wheel files define version 0.3.0rc1.
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

    # When: resolve-artifacts is executed through subprocess for the temp dist dir.
    # Then: the command prints the matching sdist and wheel paths.
    assert result.returncode == 0
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    assert lines == [str(sdist), str(wheel)]


def test_publish_workflow_uses_release_policy_and_trusted_publishing() -> None:
    # Given: the publish workflow YAML is available as text.
    text = PUBLISH_WORKFLOW.read_text()

    # When: the workflow contents are inspected for release and publish controls.
    # Then: trusted publishing and release policy steps are present.
    assert "workflow_dispatch:" in text
    assert "scripts.release_policy" in text
    assert "validate_branch_version" in text
    assert "validate_tag_version" in text
    assert "resolve-artifacts --write-github-output" in text
    assert "id-token: write" in text
    assert "pypa/gh-action-pypi-publish@release/v1" in text


def test_publish_workflow_validates_branch_and_tag_refs_separately() -> None:
    # Given: the publish workflow YAML is available as text.
    text = PUBLISH_WORKFLOW.read_text()

    # When: the branch and tag validation conditions are inspected.
    # Then: separate branch and tag guards are present.
    assert "if: ${{ github.ref_type == 'branch' }}" in text
    assert "if: ${{ github.ref_type == 'tag' }}" in text


def test_publish_workflow_handoff_uses_only_uploaded_artifacts() -> None:
    # Given: the publish job section is split from the workflow YAML.
    text = PUBLISH_WORKFLOW.read_text()
    publish_job = text.split("\n  publish:", maxsplit=1)[1]

    # When: the handoff between build and publish jobs is inspected.
    # Then: publish consumes uploaded artifacts without checkout, rebuild, or password.
    assert "needs: build" in publish_job
    assert "actions/download-artifact@v8" in publish_job
    assert "name: python-package-distributions" in publish_job
    assert "path: dist/" in publish_job
    assert "pypa/gh-action-pypi-publish@release/v1" in publish_job
    assert "actions/checkout" not in publish_job
    assert "uv build" not in publish_job
    assert "password:" not in publish_job


def test_release_verify_runs_policy_preflight_and_smoke_install() -> None:
    # Given: the release verification workflow YAML is available as text.
    text = RELEASE_VERIFY_WORKFLOW.read_text()

    # When: the workflow steps are inspected for release verification commands.
    # Then: policy preflight, artifact resolution, and wheel smoke install are present.
    assert "scripts/release_policy.py release-preflight" in text
    assert "resolve-artifacts --write-github-output" in text
    assert "Smoke install/import from built wheel" in text
    assert "pip install dist/pyrallel_consumer-*.whl" in text

from __future__ import annotations

import argparse
import os
import re
import tomllib
from pathlib import Path

from packaging.version import InvalidVersion, Version

_BRANCH_RELEASE_RE = re.compile(r"^release/(?P<major>\d+)\.(?P<minor>\d+)$")
_BRANCH_HOTFIX_RE = re.compile(
    r"^hotfix/(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$"
)
_LINE_VERSION_RE = re.compile(
    r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?P<label>a|b|rc)(?P<num>\d+)$"
)
_STABLE_VERSION_RE = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$")
_CHANGELOG_HEADING_RE = re.compile(r"^## \[([^\]]+)\]", flags=re.MULTILINE)


class PolicyError(ValueError):
    pass


def load_project_metadata(pyproject_path: str) -> tuple[str, str]:
    data = tomllib.loads(Path(pyproject_path).read_text(encoding="utf-8"))
    project = data.get("project")
    if not isinstance(project, dict):
        raise PolicyError(f"missing [project] metadata: {pyproject_path}")
    name = project.get("name")
    version = project.get("version")
    if not isinstance(name, str) or not isinstance(version, str):
        raise PolicyError(f"missing project name/version metadata: {pyproject_path}")
    return name, version


def _normalize_distribution_stem(project_name: str) -> str:
    normalized = re.sub(r"[-_.]+", "_", project_name).lower()
    if not normalized:
        raise PolicyError("project name is empty after normalization")
    return normalized


def _is_distribution_file(path: Path) -> bool:
    return path.suffix == ".whl" or path.name.endswith(".tar.gz")


def resolve_release_artifacts(
    dist_dir: str, project_name: str, version: str
) -> tuple[str, str]:
    dist_path = Path(dist_dir)
    if not dist_path.is_dir():
        raise PolicyError(f"distribution directory does not exist: {dist_dir}")

    stem = _normalize_distribution_stem(project_name)
    prefix = f"{stem}-{version}"
    sdist_path = dist_path / f"{prefix}.tar.gz"
    wheel_candidates = sorted(dist_path.glob(f"{prefix}-*.whl"))

    if not sdist_path.is_file():
        raise PolicyError(f"missing expected sdist artifact: {sdist_path}")
    if len(wheel_candidates) != 1:
        raise PolicyError(
            f"expected exactly one wheel artifact for {prefix}, found {len(wheel_candidates)}"
        )

    wheel_path = wheel_candidates[0]
    expected = {sdist_path.resolve(), wheel_path.resolve()}
    all_dist_files = sorted(
        candidate
        for candidate in dist_path.glob(f"{stem}-*")
        if candidate.is_file() and _is_distribution_file(candidate)
    )
    stale = [
        candidate for candidate in all_dist_files if candidate.resolve() not in expected
    ]
    if stale:
        stale_names = ", ".join(path.name for path in stale)
        raise PolicyError(
            f"stale distribution artifacts detected in {dist_dir}: {stale_names}"
        )

    return str(sdist_path), str(wheel_path)


def emit_github_output(sdist_path: str, wheel_path: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        raise PolicyError(
            "GITHUB_OUTPUT is required when --write-github-output is used"
        )
    with Path(output_path).open("a", encoding="utf-8") as handle:
        handle.write(f"sdist_path={sdist_path}\n")
        handle.write(f"wheel_path={wheel_path}\n")


def classify_branch(branch: str) -> str:
    if branch == "develop":
        return "develop"
    if branch == "main":
        return "main"
    if _BRANCH_RELEASE_RE.match(branch):
        return "release"
    if _BRANCH_HOTFIX_RE.match(branch):
        return "hotfix"
    raise PolicyError(f"unsupported branch: {branch}")


def _parse_version(version: str) -> Version:
    try:
        return Version(version)
    except InvalidVersion as exc:
        raise PolicyError(f"invalid version: {version}") from exc


def validate_branch_version(branch: str, version: str) -> bool:
    kind = classify_branch(branch)
    parsed = _parse_version(version)

    if kind == "develop":
        return parsed.is_prerelease and parsed.pre is not None and parsed.pre[0] == "a"
    if kind == "main":
        return not parsed.is_prerelease
    if kind == "release":
        match = _BRANCH_RELEASE_RE.match(branch)
        if match is None:
            raise PolicyError(f"invalid release branch: {branch}")
        if (parsed.major, parsed.minor) != (
            int(match.group("major")),
            int(match.group("minor")),
        ):
            return False
        return (
            parsed.is_prerelease
            and parsed.pre is not None
            and parsed.pre[0] in {"b", "rc"}
        )
    if kind == "hotfix":
        match = _BRANCH_HOTFIX_RE.match(branch)
        if match is None:
            raise PolicyError(f"invalid hotfix branch: {branch}")
        branch_major = int(match.group("major"))
        branch_minor = int(match.group("minor"))
        branch_patch = int(match.group("patch"))
        return (
            not parsed.is_prerelease
            and (parsed.major, parsed.minor) == (branch_major, branch_minor)
            and parsed.micro > branch_patch
        )
    return False


def validate_tag_version(tag: str, version: str) -> bool:
    _parse_version(version)
    return tag == f"v{version}"


def latest_concrete_changelog_heading(changelog_text: str) -> str:
    headings = _CHANGELOG_HEADING_RE.findall(changelog_text)
    concrete = [heading for heading in headings if heading.lower() != "unreleased"]
    if not concrete:
        raise PolicyError("CHANGELOG.md must contain a concrete release heading")
    return concrete[0]


def validate_changelog_version(changelog_text: str, version: str) -> bool:
    return latest_concrete_changelog_heading(changelog_text) == version


def validate_release_preflight(
    project_file: str,
    changelog_file: str,
    *,
    ref_name: str,
    ref_type: str,
) -> str:
    _, version = load_project_metadata(project_file)
    changelog_text = Path(changelog_file).read_text(encoding="utf-8")
    latest_heading = latest_concrete_changelog_heading(changelog_text)
    if latest_heading != version:
        raise PolicyError(
            "CHANGELOG latest heading %r != pyproject version %r"
            % (latest_heading, version)
        )

    if ref_type == "tag":
        if not validate_tag_version(ref_name, version):
            raise PolicyError(
                "Tag/version policy mismatch: tag=%r version=%r" % (ref_name, version)
            )
    elif ref_type == "branch":
        if not validate_branch_version(ref_name, version):
            raise PolicyError(
                "Branch/version policy mismatch: branch=%r version=%r"
                % (ref_name, version)
            )
    else:
        raise PolicyError(f"unsupported ref type: {ref_type}")

    return version


def validate_pull_request_flow(base_branch: str, head_branch: str) -> bool:
    """Validate git-flow pull-request routing.

    Current hard gate:
    - PRs into ``main`` are allowed only from ``release/x.y`` or ``hotfix/x.y.z``.
    - Other supported base branches are not constrained at this layer.
    """
    base_kind = classify_branch(base_branch)
    if base_kind != "main":
        return True
    return bool(_BRANCH_RELEASE_RE.match(head_branch)) or bool(
        _BRANCH_HOTFIX_RE.match(head_branch)
    )


def _bump_line(version: str, expected_label: str) -> str:
    match = _LINE_VERSION_RE.match(version)
    if match is None:
        raise PolicyError(f"version is not a valid prerelease line: {version}")
    if match.group("label") != expected_label:
        raise PolicyError(f"version does not use {expected_label!r}: {version}")
    return (
        f"{match.group('major')}.{match.group('minor')}.{match.group('patch')}"
        f"{expected_label}{int(match.group('num')) + 1}"
    )


def bump_alpha(version: str) -> str:
    return _bump_line(version, "a")


def bump_beta(version: str) -> str:
    return _bump_line(version, "b")


def bump_rc(version: str) -> str:
    return _bump_line(version, "rc")


def bump_patch(version: str) -> str:
    match = _STABLE_VERSION_RE.match(version)
    if match is None:
        raise PolicyError(f"version is not a stable patch line: {version}")
    return (
        f"{match.group('major')}.{match.group('minor')}.{int(match.group('patch')) + 1}"
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate release policy branch/tag rules."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    branch_parser = subparsers.add_parser("validate-branch-version")
    branch_parser.add_argument("--branch", required=True)
    branch_parser.add_argument("--version", required=True)

    pr_flow_parser = subparsers.add_parser("validate-pr-flow")
    pr_flow_parser.add_argument("--base-branch", required=True)
    pr_flow_parser.add_argument("--head-branch", required=True)

    tag_parser = subparsers.add_parser("validate-tag-version")
    tag_parser.add_argument("--tag", required=True)
    tag_parser.add_argument("--version", required=True)

    preflight_parser = subparsers.add_parser("release-preflight")
    preflight_parser.add_argument("--project-file", default="pyproject.toml")
    preflight_parser.add_argument("--changelog-file", default="CHANGELOG.md")
    preflight_parser.add_argument("--ref-name", required=True)
    preflight_parser.add_argument(
        "--ref-type", required=True, choices=["branch", "tag"]
    )

    artifacts_parser = subparsers.add_parser("resolve-artifacts")
    artifacts_parser.add_argument("--dist-dir", default="dist")
    artifacts_parser.add_argument("--project-file", default="pyproject.toml")
    artifacts_parser.add_argument(
        "--write-github-output",
        action="store_true",
        help="Write artifact paths to GITHUB_OUTPUT for GitHub Actions.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "validate-branch-version":
            is_valid = validate_branch_version(args.branch, args.version)
            if is_valid:
                print("OK")
                return 0
            print("INVALID")
            return 1
        if args.command == "validate-tag-version":
            is_valid = validate_tag_version(args.tag, args.version)
            if is_valid:
                print("OK")
                return 0
            print("INVALID")
            return 1
        if args.command == "validate-pr-flow":
            is_valid = validate_pull_request_flow(args.base_branch, args.head_branch)
            if is_valid:
                print("OK")
                return 0
            print("INVALID")
            return 1
        if args.command == "release-preflight":
            version = validate_release_preflight(
                args.project_file,
                args.changelog_file,
                ref_name=args.ref_name,
                ref_type=args.ref_type,
            )
            print("OK: %s" % version)
            return 0

        project_name, version = load_project_metadata(args.project_file)
        sdist_path, wheel_path = resolve_release_artifacts(
            args.dist_dir, project_name, version
        )
        print(sdist_path)
        print(wheel_path)
        if args.write_github_output:
            emit_github_output(sdist_path, wheel_path)
        return 0
    except PolicyError as exc:
        print(f"INVALID: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone


def git_output(*args: str) -> str | None:
    """Handle git output within benchmark artifacts."""
    try:
        completed = subprocess.run(
            ["git", *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    value = completed.stdout.strip()
    return value or None


def build_artifact_metadata(
    *,
    output_path: str,
    environ: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build artifact metadata for benchmark artifacts."""
    env = os.environ if environ is None else environ
    metadata: dict[str, str] = {
        "artifact_path": output_path,
        "execution_context": (
            "github_actions" if env.get("GITHUB_ACTIONS") == "true" else "local"
        ),
        "generated_at_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    }

    field_map = {
        "PYRALLEL_BENCHMARK_ARTIFACT_NAME": "artifact_name",
        "GITHUB_EVENT_NAME": "github_event_name",
        "GITHUB_JOB": "github_job",
        "GITHUB_REPOSITORY": "github_repository",
        "GITHUB_RUN_ATTEMPT": "github_run_attempt",
        "GITHUB_RUN_ID": "github_run_id",
        "GITHUB_SHA": "git_commit_sha",
        "GITHUB_REF": "git_ref",
        "GITHUB_REF_NAME": "git_ref_name",
        "GITHUB_REF_TYPE": "git_ref_type",
        "GITHUB_WORKFLOW": "github_workflow",
        "GITHUB_WORKFLOW_REF": "github_workflow_ref",
    }
    for env_name, metadata_key in field_map.items():
        value = env.get(env_name)
        if value:
            metadata[metadata_key] = value

    if "git_commit_sha" not in metadata:
        git_commit_sha = git_output("rev-parse", "HEAD")
        if git_commit_sha is not None:
            metadata["git_commit_sha"] = git_commit_sha

    git_ref_name = metadata.get("git_ref_name")
    git_ref_type = metadata.get("git_ref_type")
    if git_ref_name is None or git_ref_type is None:
        git_tag = git_output("describe", "--tags", "--exact-match")
        git_branch = git_output("symbolic-ref", "--quiet", "--short", "HEAD")
        if git_ref_name is None:
            if git_tag is not None:
                metadata["git_ref_name"] = git_tag
            elif git_branch is not None:
                metadata["git_ref_name"] = git_branch
        if git_ref_type is None:
            if git_tag is not None:
                metadata["git_ref_type"] = "tag"
            elif git_branch is not None:
                metadata["git_ref_type"] = "branch"

    if "git_ref" not in metadata:
        git_ref_name = metadata.get("git_ref_name")
        git_ref_type = metadata.get("git_ref_type")
        if git_ref_name is not None and git_ref_type == "branch":
            metadata["git_ref"] = "refs/heads/%s" % git_ref_name
        elif git_ref_name is not None and git_ref_type == "tag":
            metadata["git_ref"] = "refs/tags/%s" % git_ref_name

    return metadata

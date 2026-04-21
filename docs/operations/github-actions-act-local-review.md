# GitHub Actions Local Review with `act`

This document captures the minimum local procedure for quickly reviewing GitHub
Actions workflows with `act`.

## 1. Objective

- Run a first-pass check of workflow structure and step wiring locally before
  pushing or opening a PR.
- Especially when changing `.github/workflows/*.yml`, verify that jobs and steps
  are still parsed correctly.

## 2. Prerequisites

- Docker daemon is running
- `act` is installed (`act --version`)
- On Apple Silicon (M-series), default to
  `--container-architecture linux/amd64`

## 3. Quick Checks

### 3.1 List workflows and jobs

```bash
act -l
```

### 3.2 CI workflow dry-run

```bash
act pull_request \
  -W .github/workflows/ci.yml \
  --dryrun \
  -P ubuntu-latest=catthehacker/ubuntu:act-latest \
  --container-architecture linux/amd64
```

### 3.3 E2E workflow dry-run

```bash
act pull_request \
  -W .github/workflows/e2e.yml \
  --dryrun \
  -P ubuntu-latest=catthehacker/ubuntu:act-latest \
  --container-architecture linux/amd64
```

## 4. When You Need an Explicit Event Payload

Some jobs (for example `release_policy` in `ci.yml`) read
`pull_request.base.ref` / `pull_request.head.ref` directly. In that case,
`act`'s default event payload may be insufficient. Inject a test event file.

```bash
cat > .tmp/act-pr-event.json <<'JSON'
{
  "pull_request": {
    "base": { "ref": "develop" },
    "head": { "ref": "feat/local-act-check" }
  }
}
JSON

act pull_request \
  -W .github/workflows/ci.yml \
  -j release_policy \
  -e .tmp/act-pr-event.json \
  -P ubuntu-latest=catthehacker/ubuntu:act-latest \
  --container-architecture linux/amd64
```

## 5. Operational Notes

- Local `act` results are not 100% identical to GitHub-hosted runners.
- The goal is a fast pre-check for pipeline shape and script regressions.
  Final judgment still comes from real GitHub Actions runs.
- If runtime differences appear in post steps (for example image-dependent post
  behavior in `actions/setup-python`), keep local checks focused on dry-run and
  critical steps, then confirm on GitHub-hosted runners.

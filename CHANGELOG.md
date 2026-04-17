# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Stable release readiness checklist in `docs/operations/release-readiness.md`
- `SECURITY.md` for private vulnerability reporting and support scope

### Changed
- CI quality gate will validate lint/type/security/build/artifact checks before stable promotion

## [1.0.0] - 2026-04-17

### Changed
- Promoted package metadata from prerelease to stable (`version = 1.0.0`, `Development Status :: 5 - Production/Stable`)
- Aligned release posture language in `README.md` and `README.ko.md` with the stable support policy

### Migration notes
- If you pinned `pyrallel-consumer==0.1.2a2`, upgrade to `pyrallel-consumer==1.0.0`
- Keep Python `>=3.12` (unchanged from the prerelease line)
- Regenerate lockfiles/environments after upgrade (`uv sync`)

## [0.1.2a2] - 2026-03-27

### Changed
- Published prerelease `0.1.2a2`
- Updated README release policy to explicitly describe alpha/hardening status

# Internal / Legacy Document Language Policy

This document records the repository rule for internal and legacy markdown
surfaces that are not handled as user-facing localized manuals. The
operations-facing copy lives in
[operations/language-policy.md](./operations/language-policy.md).

## Rule

- unsuffixed `.md` files are the English canonical documents.
- In other words, each unsuffixed `.md` path stays English-first.
- Korean mirrors must use the sibling `*.ko.md` filename.
- Entry-point links should keep the unsuffixed path stable for English readers
  and link directly to the `.ko.md` mirror when a Korean path is needed.

## Current Internal / Legacy Scope

- `prd.md`: [../prd.md](../prd.md) with preserved Korean source at
  [../prd.ko.md](../prd.ko.md)
- `prd_dev.md`: [../prd_dev.md](../prd_dev.md) with preserved Korean source at
  [../prd_dev.ko.md](../prd_dev.ko.md)
- [blueprint/00-index.md](./blueprint/00-index.md) and the rest of
  `docs/blueprint/**`, which already follow the same unsuffixed-English /
  `.ko.md` mirror pattern

## Explicit Exemptions

- `GEMINI.md` is an in-repo handoff and working-log document. It is exempt from
  the filename-language rule.
- `docs/plans/**` is an archival design and execution record tree. It is exempt
  from the filename-language rule and may retain mixed-language historical
  content.

## Validation

- [../tests/unit/test_internal_doc_language_assets.py](../tests/unit/test_internal_doc_language_assets.py)
  verifies the PRD mirror pair, the policy rule text, and curated link targets.
- [operations/language-policy.md](./operations/language-policy.md) republishes
  the same rule from the `docs/operations/` entry surface.
- [../tests/unit/test_blueprint_language_assets.py](../tests/unit/test_blueprint_language_assets.py)
  verifies the existing blueprint tree.

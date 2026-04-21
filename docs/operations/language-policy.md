# Language Policy

This document codifies the repository filename-language rule for internal and
legacy markdown surfaces.

## Canonical Rule

- unsuffixed `.md` files are the English canonical documents.
- Korean mirrors must use the sibling `*.ko.md` filename.
- Entry-point links should keep the unsuffixed English path stable and add an
  explicit `.ko.md` link when Korean readers need the preserved source text.

## Internal / Legacy PRD Scope

- [`prd.md`](../../prd.md) is the English canonical rationale entry.
  Preserved Korean source: [`prd.ko.md`](../../prd.ko.md)
- [`prd_dev.md`](../../prd_dev.md) is the English canonical developer-spec
  entry. Preserved Korean source:
  [`prd_dev.ko.md`](../../prd_dev.ko.md)
- [`docs/blueprint/00-index.md`](../blueprint/00-index.md) and the rest of
  `docs/blueprint/**` already follow the same unsuffixed-English / `.ko.md`
  mirror pattern.

## Explicit Exemptions

- `GEMINI.md` is a handoff and working-log artifact. It is intentionally exempt
  from the filename-language rule.
- `docs/plans/**` is an archival plan tree. It is intentionally exempt from the
  filename-language rule and may retain historical mixed-language content.

## Validation

- [`../../tests/unit/test_internal_doc_language_assets.py`](../../tests/unit/test_internal_doc_language_assets.py)
  verifies the PRD mirror pair, policy wording, surface links, and local link
  integrity for this policy.
- [`../internal-doc-language-policy.md`](../internal-doc-language-policy.md)
  keeps the same rule available from the legacy internal-doc entry path.

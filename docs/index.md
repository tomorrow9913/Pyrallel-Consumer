# Docs Index

`docs/` is organized into three document groups.

## 1. Blueprint

- **[blueprint/00-index.md](./blueprint/00-index.md)** - main entry point for service definition, requirements, architecture, and feature blueprints

This group includes:

- overview / requirements / architecture / open decisions / context restoration
- per-feature flow: `00-index -> 01-requirements -> 02-architecture -> 03-design`

## 2. Operations

- **[../SECURITY.md](../SECURITY.md)** - private vulnerability reporting path and security response policy
- **[internal-doc-language-policy.md](./internal-doc-language-policy.md)** - legacy internal-doc mirror for the filename-language rule and exemptions
- **[operations/index.md](./operations/index.md)** - operations document entry point
- **[operations/guide.ko.md](./operations/guide.ko.md)** - Korean operations guide
- **[operations/language-policy.md](./operations/language-policy.md)** - filename-language rule and explicit exemptions for internal / legacy documents
- **[operations/guide.en.md](./operations/guide.en.md)** - English operations guide
- **[operations/compatibility-matrix.md](./operations/compatibility-matrix.md)** - automated Kafka/Python/client verification combinations
- **[operations/playbooks.md](./operations/playbooks.md)** - operations playbooks and tuning procedures
- **[operations/public-contract-v1.md](./operations/public-contract-v1.md)** - v1 ordering/rebalance/DLQ/commit public contract freeze
- **[operations/oss-messaging-guardrails.md](./operations/oss-messaging-guardrails.md)** - inbound-only OSS messaging policy guardrails
- **[operations/wave-2-gate-cadence.md](./operations/wave-2-gate-cadence.md)** - wave-2 resume gate/cadence rules after MQU-4
- **[operations/release-readiness.md](./operations/release-readiness.md)** - stable release readiness checklist
- **[operations/support-policy.md](./operations/support-policy.md)** - Python/Kafka support scope and compatibility policy
- **[operations/upgrade-rollback-guide.md](./operations/upgrade-rollback-guide.md)** - upgrade/rollback operations guide
- **[operations/github-actions-act-local-review.md](./operations/github-actions-act-local-review.md)** - local `act` workflow review guide

## 3. Plans

- **[plans/](./plans/)** - date-based design/implementation plan archive

## Quick Guide

- For current runtime structure and feature blueprints, start at `blueprint/`.
- For operations and incident response only, start at `operations/`.
- For archived plans and design decisions, check `plans/`.

# Operations Docs

Operations documents are grouped under `docs/operations/`.

Repository-level security reporting policy:

- **[../../SECURITY.md](../../SECURITY.md)** - private vulnerability reporting path and response window

- **[guide.ko.md](./guide.ko.md)** - Korean operations guide
- **[guide.en.md](./guide.en.md)** - English operations guide
- **[playbooks.md](./playbooks.md)** - operations playbooks and tuning procedures
- **[public-contract-v1.md](./public-contract-v1.md)** - v1 public contract freeze criteria before stable promotion
- **[oss-messaging-guardrails.md](./oss-messaging-guardrails.md)** - inbound-only OSS messaging guardrails (no outbound contribution asks)
- **[wave-2-gate-cadence.md](./wave-2-gate-cadence.md)** - wave-2 start gate/cadence operations rules after MQU-4 closure
- **[support-policy.md](./support-policy.md)** - Python/Kafka support scope and compatibility policy
- **[upgrade-rollback-guide.md](./upgrade-rollback-guide.md)** - upgrade/rollback procedure guide
- **[release-versioning-policy.md](./release-versioning-policy.md)** - branch/version bump/PyPI publish policy
- **[release-readiness.md](./release-readiness.md)** - prioritized checklist before stable release promotion
- **[github-actions-act-local-review.md](./github-actions-act-local-review.md)** - local `act`-based GitHub Actions review guide

Quick navigation:

- For operational metrics and interpretation baselines, start with `guide.*.md`.
- For incident response, profile-based recommended settings, and tuning procedures, use `playbooks.md`.
- For ordering/rebalance/DLQ/commit public contract freeze criteria, use `public-contract-v1.md`.
- For OSS external messaging guardrails, use `oss-messaging-guardrails.md`.
- For wave-2 resume conditions and follow-up cadence, use `wave-2-gate-cadence.md`.
- For support scope and compatibility rules, use `support-policy.md`.
- For pre/post-promotion operations and recovery criteria, use `upgrade-rollback-guide.md`.
- For branch strategy, version bump rules, and PyPI publish policy, use `release-versioning-policy.md`.
- For pre-release checks and evidence requirements, use `release-readiness.md`.
- For local pre-push workflow sanity checks with `act`, use `github-actions-act-local-review.md`.

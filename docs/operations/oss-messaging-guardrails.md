# OSS Messaging Guardrails (Inbound-only)

This sprint guardrail enforces the board directive from [MQU-4](/MQU/issues/MQU-4):

- open-source launch is allowed
- outbound messaging that asks external people to contribute is not allowed

## Policy Scope

- Scope: repository README/docs and release-facing public copy
- Owner: CMO lane
- Effective: immediately for this sprint

## Allowed Messaging

- explain project capabilities, architecture, support boundaries, and release policy
- publish docs and examples that improve discoverability for inbound users
- describe how maintainers review inbound issues/PRs when external users open them voluntarily

## Disallowed Messaging

- direct asks such as "please contribute", "PRs welcome", "join and contribute"
- campaigns or outreach that request outside contribution
- copy that frames external contribution as a call-to-action

## Required Copy Pattern

When contribution policy is mentioned in public docs, use this framing:

- "inbound-only OSS messaging policy"
- maintainers review inbound issues/PRs opened by external users
- no outbound contribution request campaigns

## Outbound Lane Alignment

The following outbound-lane issues must align to this policy:

- [MQU-10](/MQU/issues/MQU-10): remove or rewrite outbound contribution asks to inbound-only wording
- [MQU-125](/MQU/issues/MQU-125): keep distribution/promotions informational; no contribution CTA language

## Evidence For This Issue

- README contribution section rewritten to inbound-only language
- README.ko contribution section rewritten to inbound-only language
- policy index links updated to include this guardrail document

# Security Policy

## Supported Versions

This security support matrix is aligned with
`docs/operations/support-policy.md`.

### Current prerelease-only phase (before first stable release)

| Version | Supported |
| --- | --- |
| latest prerelease | Yes |
| older prerelease builds | Best effort |

### Stable launch policy (effective once `1.0.0` ships)

| Version line | Security support |
| --- | --- |
| latest stable minor | Yes |
| previous stable minor | Security fixes only |
| prerelease builds newer than latest stable | Best effort |
| older prerelease builds | Best effort |

## Reporting A Vulnerability

Do not report security issues in public GitHub issues.

Preferred private channels:

1. GitHub repository `Security` tab -> `Report a vulnerability`
2. GitHub private security advisory draft for this repository

If private reporting is unavailable in your environment, contact repository
maintainers privately first and avoid posting exploit details in public threads.

- Helpful details to include
  - affected scope
  - reproduction steps
  - logs or stack traces
  - possible mitigations

- Expected response window
  - acknowledgement: target within 3 business days
  - initial triage: target within 7 business days

## Scope Notes

- Treat Kafka topic names, DLQ topic suffixes, and serialization payloads as part
  of input validation.
- Never commit secrets to the repository; use environment variables or a deployment
  secret store.
- Configure secured Kafka clusters through the allowlisted `KafkaConfig` TLS/SASL
  fields documented in `docs/operations/secure-kafka-config.md`; do not rely on
  local patches that log or snapshot raw client configs.
- Treat `KAFKA_SASL_PASSWORD`, `KAFKA_SSL_KEY_PASSWORD`, client private-key
  files, and key/certificate paths as sensitive operational material.
- In production, evaluate `KAFKA_DLQ_PAYLOAD_MODE=metadata_only` first to reduce
  sensitive payload exposure.

## Coordinated Disclosure

By default, vulnerability details remain private until a fix is released. After a
patched version ships, summarize impact scope and mitigations in the changelog or
release notes.

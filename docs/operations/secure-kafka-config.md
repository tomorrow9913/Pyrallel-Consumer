# Secure Kafka Configuration

This guide documents the Issue #76 secure Kafka configuration surface and the
review expectations for changes that touch Kafka client configuration.

## Supported allowlist

`KafkaConfig` exposes only the librdkafka security keys listed below. The same
security map must be merged into consumer, producer, and admin client configs so
DLQ publishing, commits, metadata/admin operations, and the main consumer all use
the same connection posture.

| `KafkaConfig` field | Environment variable | librdkafka key | Sensitive? | Purpose |
| --- | --- | --- | --- | --- |
| `security_protocol` | `KAFKA_SECURITY_PROTOCOL` | `security.protocol` | No | Select plaintext, TLS, SASL, or SASL over TLS. |
| `sasl_mechanisms` | `KAFKA_SASL_MECHANISMS` | `sasl.mechanisms` | No | SASL mechanism, for example `PLAIN`, `SCRAM-SHA-256`, or `SCRAM-SHA-512`. |
| `sasl_username` | `KAFKA_SASL_USERNAME` | `sasl.username` | Yes | SASL principal. Treat as sensitive in logs and support bundles. |
| `sasl_password` | `KAFKA_SASL_PASSWORD` | `sasl.password` | Yes | SASL secret. Never log or include in runtime snapshots. |
| `ssl_ca_location` | `KAFKA_SSL_CA_LOCATION` | `ssl.ca.location` | Usually no | CA bundle path used to validate broker certificates. |
| `ssl_certificate_location` | `KAFKA_SSL_CERTIFICATE_LOCATION` | `ssl.certificate.location` | Usually yes | Client certificate path for mTLS. |
| `ssl_key_location` | `KAFKA_SSL_KEY_LOCATION` | `ssl.key.location` | Yes | Client private key path for mTLS. |
| `ssl_key_password` | `KAFKA_SSL_KEY_PASSWORD` | `ssl.key.password` | Yes | Secret for encrypted client private keys. |

Do not add generic passthrough configuration for this issue. Any new Kafka
client key should be explicitly modeled, documented, and covered by tests before
it is admitted to this allowlist.

## Example deployments

### SASL over TLS

```dotenv
KAFKA_BOOTSTRAP_SERVERS=broker-1.example.com:9093,broker-2.example.com:9093
KAFKA_SECURITY_PROTOCOL=SASL_SSL
KAFKA_SASL_MECHANISMS=SCRAM-SHA-512
KAFKA_SASL_USERNAME=pyrallel-consumer
KAFKA_SASL_PASSWORD=${KAFKA_SASL_PASSWORD}
KAFKA_SSL_CA_LOCATION=/etc/pyrallel/kafka/ca.pem
```

### mTLS

```dotenv
KAFKA_BOOTSTRAP_SERVERS=broker-1.example.com:9093
KAFKA_SECURITY_PROTOCOL=SSL
KAFKA_SSL_CA_LOCATION=/etc/pyrallel/kafka/ca.pem
KAFKA_SSL_CERTIFICATE_LOCATION=/etc/pyrallel/kafka/client.crt
KAFKA_SSL_KEY_LOCATION=/etc/pyrallel/kafka/client.key
KAFKA_SSL_KEY_PASSWORD=${KAFKA_SSL_KEY_PASSWORD}
```

## Secret-handling rules

- Store passwords and private-key material in a deployment secret manager or
  injected environment variables, not in committed files.
- Keep `.env` local and untracked; `.env.sample` may show variable names but must
  not contain real values.
- Do not log raw Kafka client configuration if it may contain secret values
  such as `sasl.password` or `ssl.key.password`. Treat `sasl.username`,
  certificate paths, and key paths as sensitive operational metadata when
  sharing support bundles or screenshots.
- Do not expose secure connection fields in runtime snapshots or public metrics.
  If diagnostics need to confirm that secure mode is enabled, report coarse
  state such as `security_protocol` without principal, path, or secret values.

## Code review checklist

Use this checklist when reviewing secure Kafka config changes:

1. `KafkaConfig.get_consumer_config()`, `get_producer_config()`, and any admin
   client config path receive the same allowlisted security keys.
2. Plaintext local defaults remain unchanged when no secure fields are set.
3. Optional fields with `None` or empty values are omitted rather than forwarded
   as blank librdkafka settings.
4. Secrets are not emitted through logging, model dumps used for diagnostics,
   runtime snapshots, metrics labels, exception messages, or test snapshots.
5. Tests cover constructor values, environment-variable values, consumer config,
   producer config, admin config, and redaction/no-leak behavior.
6. Documentation updates include README, `.env.sample`, this operations guide,
   and the public-contract document when the public config surface changes.

## Review result for Issue #76

The pre-change code quality review found the issue root cause in
`pyrallel_consumer/config.py`: `KafkaConfig` centralizes Kafka client settings,
but the existing producer and consumer config builders only emitted bootstrap,
client/group, offset, auto-commit, and session-timeout fields. The fix should
therefore stay localized to the `KafkaConfig` public surface plus the admin
config creation path, rather than coupling the control plane to execution-engine
or deployment-specific details.

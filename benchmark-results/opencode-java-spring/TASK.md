# OpenCode WM Adapter QA benchmark

Build a production-grade Spring Boot 3.3 / Java 21 multi-tenant flash-sale order and inventory service. Use PostgreSQL + Flyway, Redis-backed idempotency/locks behind an interface, and Kafka-compatible event publishing behind an interface. Implement REST APIs for inventory, idempotent order creation, payment webhooks, cancellation and expiry. Enforce tenant isolation, validation, RFC 9457 errors, pagination, secure defaults, observability, and OpenAPI.

The hard invariants are: concurrent reservations must never oversell; a tenant and idempotency key must have exactly one business effect; webhook replays must be harmless; payment/cancellation races must resolve legally; retries must not duplicate events or reservations.

Include unit, MockMvc, repository, Testcontainers PostgreSQL, deterministic concurrency, replay, race, tenant-isolation, and failure/retry tests. Provide README architecture and exact commands. Do not claim completion without running compile and tests. Work only in this directory.

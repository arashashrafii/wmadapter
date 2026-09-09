# ADR: Verified dedicated browser distribution (superseded)

Status: superseded by `ADR-managed-runtime.md`

This historical proposal is no longer the runtime contract. Web Model Adapter now
requires the user's installed Google Chrome and does not download or install a
Playwright browser artifact. The current installer stops with instructions when
Google Chrome is absent.

The cache key contains OS, architecture, Playwright version, and browser hash.
Artifacts are staged in a temporary directory, checked for size and SHA256,
and activated atomically. Resumable downloads, bounded retries/timeouts, and a
separate proxy are installer transport responsibilities. A manifest signature
must be verified with a key supplied by the release environment; no signing
secret or public trust root is stored in this repository.

## Verified local bundle slice

| Platform | Gateway installer | Dedicated browser | Notes |
|---|---|---|---|
| Ubuntu 24.04 x86-64 | Adapter contract | Playwright Chromium | Local offline/cache bundle contract only. |

The current implementation validates the manifest schema, host platform,
artifact size and SHA256, local cache identity, staging, preflight, and atomic
activation. Publishing real artifacts, CDN/mirror endpoints, resumable
transport, release signing, macOS, Windows, and public release packaging are
outside this slice.

# ADR: Verified dedicated browser distribution

Status: accepted (release contract; artifact publication pending)

MimicGate uses only a dedicated Playwright browser artifact. It never falls
back to Chrome or Chromium discovered on the user's PATH. Distribution lookup
checks sources in this order: offline bundle, verified local cache, primary
CDN, then a signed mirror.

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

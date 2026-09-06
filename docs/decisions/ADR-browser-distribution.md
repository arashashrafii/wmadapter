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

## Support matrix

| Platform | Gateway installer | Dedicated browser | Notes |
|---|---|---|---|
| Linux | Adapter contract | Playwright Chromium | `install.sh` is the current local adapter. |
| macOS | Adapter contract | Playwright Chromium | Native packaging is pending. |
| Windows | Adapter contract | Playwright Chromium | Native packaging is pending. |

The current implementation provides testable integrity, cache ordering,
staging, and adapter contracts. Publishing real artifacts, CDN/mirror
endpoints, resumable transport, and release signing are intentionally mocked
until the release environment supplies them.

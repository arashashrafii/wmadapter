# ADR: Dedicated managed browser runtime

Status: accepted (phase 1)

Web Model Adapter managed mode uses the Chromium binary installed by Playwright. It
does not search for or attach to a user's Chrome/Chromium installation. The
installer provisions that binary with `playwright install chromium` and starts
login through the existing Playwright controller.

Each provider receives a separate canonical profile under
`~/.local/share/wmadapter/profiles/<provider>`. The profile is never the user's
default browser profile. A provider profile is protected by an advisory lock
and diagnostic metadata containing only owner PID/process group, executable,
profile, mode, start time, and a lock token. Stale metadata may be replaced
after acquiring the lock; profile data and Chromium Singleton files are never
deleted automatically.

Managed mode is the default. CDP remains an explicit attach mode only when
`browser.mode: cdp` and `browser.cdp_endpoint` are configured; it does not
acquire managed locks or close the operator's browser. The installer no longer
creates or assumes a fixed CDP/9222 endpoint.

The headed login and headless runtime reuse the same provider profile and
Playwright browser installation. Authentication remains user-driven, including
CAPTCHA. A future phase may add a dedicated supervisor process for stronger
cross-platform process-group reaping; phase 1 establishes the path, lock, and
binary ownership contract.

Migration safety: existing profiles and credentials are not moved or removed by
the installer. Operators must stop the old runtime, verify the lock owner, and
make a permission-preserving backup before any manual migration.

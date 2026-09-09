# Web Model Adapter Roadmap

This roadmap is organized so coding can continue directly from GitHub issues.

## Milestone 1 — Proof of Concept — COMPLETE

Validated live on Linux with DeepSeek Web. The end-to-end browser flow returned `OK`.

Completed issues:
- #1 POC: Validate DeepSeek Web OpenAI-compatible bridge
- #2 Implement DeepSeek Adapter MVP

## Milestone 2 — Stable Local Tool

Goal: make the DeepSeek bridge reliable enough for daily use.

Issues:
- #3 Harden DeepSeek session and authentication recovery
- #4 Deliberately rejected: provider credential storage is out of scope; use
  browser-only authentication and browser-managed session state.
- #5 Improve reliability, configuration, logging, and conversation handling

Exit criteria:
- Restart/recovery works without manual browser intervention in normal cases.
- Provider passwords are never accepted or stored; browser session state is isolated.
- Repeated conversations work reliably in one running process.

## Milestone 3 — Agent Compatibility

Goal: make existing OpenAI-compatible clients and agents use the bridge with minimal changes.

Issues:
- #6 Complete OpenAI-compatible chat/completions behavior
- #7 Add streaming responses and long-running agent stability
- #8 Validate compatibility with agent clients

Exit criteria:
- OpenAI Python SDK works against the local base URL for documented features.
- Streaming is supported or has a documented fallback.
- At least one real agent/client is validated end-to-end.

## Milestone 4 — Multi-Provider Bridge

Goal: move from a DeepSeek-specific bridge to a provider-independent local gateway.

Issues:
- #9 Introduce generic provider adapter contract
- #10 Add second browser-chat provider adapter
- #11 Add provider routing, fallback policy, and multi-provider tests

Exit criteria:
- DeepSeek and at least one additional provider work through the same API.
- Core API code contains no provider-specific browser logic.
- Provider selection and failure behavior are deterministic and tested.

## Milestone 5 — Public Product Version

Goal: make the project installable and safe enough for external self-hosted users.

Issues:
- #13 Add multi-user credential/session isolation
- #14 Finalize public documentation, security guidance, and maintenance policy

Exit criteria:
- Fresh Linux installation is reproducible from docs.
- User/account sessions and secrets are isolated.
- Operational, security, upgrade, and maintenance documentation is complete.

## Recommended execution order

1. #3
2. #4
3. #5
4. #6
5. #7
6. #8
7. #9
8. #10
9. #11
10. #12
11. #13
12. #14

Do not start a later milestone until the previous milestone exit criteria pass, unless a task is explicitly independent.

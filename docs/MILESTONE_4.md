# Milestone 4 Provider Expansion Foundation

Implemented:

- Generic `ChatProvider` contract.
- Provider router with model-to-provider dispatch hooks.
- DeepSeek service implements the provider contract.
- Configuration includes provider defaults and enabled provider list.

Current provider:

- `deepseek`

Future provider adapters should implement `ChatProvider` and be registered in the MimicGate application.

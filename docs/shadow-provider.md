# JDO-10 — OpenAI-compatible LLM shadow provider

## Purpose

`LlmShadowProvider` runs the same validated Decision Contract and state through the official [System One Adapter](https://github.com/typesafe-ai/system-one-adapter-python). It is an independent shadow path: it returns its own `ProviderResult` or sanitized `ProviderFailure`; it never replaces Jev output or changes a policy decision.

V0 selects **one** adapter extra: `system-one-adapter[openai]==0.2.0`. Anthropic is deliberately not configured in this release. The OpenAI-compatible adapter path supports the OpenAI endpoint and an explicitly configured compatible base URL.

## Configuration

| Environment variable | Default | Meaning |
| --- | --- | --- |
| `SHADOW_PROVIDER_ENABLED` | `false` | Explicit opt-in. A disabled provider makes no client or network call. |
| `SHADOW_OPENAI_API_KEY` | empty | Required only after the provider is enabled; never logged or persisted. |
| `SHADOW_OPENAI_MODEL` | `gpt-4o-mini` | Requested model name recorded in shadow result metadata. |
| `SHADOW_OPENAI_BASE_URL` | empty | Optional OpenAI-compatible endpoint. |
| `SHADOW_TIMEOUT_SECONDS` | `20` | Bounded to `(0, 60]`. |
| `SHADOW_MAX_RETRIES` | `1` | Bounded transient retry count, `0..3`. |
| `SHADOW_MALFORMED_RETRIES` | `1` | Bounded corrective structured-output retry count, `0..3`. |

The provider uses `AsyncSystemOneAdapterClient` with native structured outputs, `llm_answer_mode="probabilities"`, and `normalize_probabilities=False`. A malformed or invalid distribution therefore remains observable as a provider-result validation issue in JDO-9 rather than being silently rewritten.

## Result and audit boundary

Successful shadow output maps to the existing provider-independent typed answers. Its safe metadata records `provider=openai_compatible_shadow`, requested and resolved model, adapter version, cumulative input/output token totals across retries, and adapter latency. A shadow failure has the same safe provider identifier, so JDO-7 persistence will not misclassify it as Jev.

The adapter can expose `debug.llm_attempts`, retry reasons, prompt messages, request parameters, and raw LLM responses. DecisionOps never reads, maps, logs, exports, or persists that debug object. Raw state and API keys are also excluded from the common result and audit models.

## Test and live-call boundary

All unit tests inject a fake async adapter client and construct official adapter response models locally. They make no network request and require no external API key. A real shadow smoke test needs explicit enablement and an untracked environment file; it is outside normal CI.

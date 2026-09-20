# JDO-5 — Official Jev provider integration

## Purpose

`JevDecisionProvider` converts a validated V1 Decision Contract into the
official asynchronous TypeSafe SDK's `Noul`, `Choice`, and `Score` questions.
Questions sharing one state are sent in one `system_one` request. The adapter
then returns provider-independent typed answers for the future policy engine.

## Configuration

| Environment variable | Default | Boundary |
| --- | --- | --- |
| `TYPESAFE_API_KEY` | empty | Required only for a live call; never logged or persisted. |
| `TYPESAFE_MODEL` | `jev-latest` | Requested alias recorded in result metadata. |
| `TYPESAFE_TIMEOUT_SECONDS` | `10` | Bounded to `(0, 60]`. |
| `TYPESAFE_MAX_RETRIES` | `2` | Bounded to `0..3`; uses bounded SDK retry/backoff. |

The provider forces the `typesafe_sdk` logger to `WARNING` by default. SDK debug
logging can expose request/response bodies and is not a production-safe default.

## Result mapping

On success the common `ProviderResult` contains:

- typed `NoulAnswer`, `ChoiceAnswer`, or `ScoreAnswer` values;
- requested model alias and resolved response model;
- request ID when the SDK supplies one;
- SDK version, input/output token metadata when supplied, and monotonic latency.

The adapter stores no raw provider body, request state, API key, authorization
header, or SDK debug payload.

## Failure mapping

| SDK/config condition | Common failure kind | Policy outcome? |
| --- | --- | --- |
| Missing API key | `configuration` | No |
| Authentication error | `authentication` | No |
| Rate limit | `rate_limited` | No |
| Timeout | `timeout` | No |
| Connection error | `transport` | No |
| Missing/invalid typed answer | `invalid_response` | No |

A provider failure does not become `ACT`, `REVIEW`, or `FALLBACK`. The policy
engine in JDO-6 will receive only a valid `ProviderResult`.

## Test boundary

All JDO-5 tests inject an async fake client and instantiate official SDK response
models locally. They make no network request and require no API key. A live
smoke test remains an explicit later workflow, outside normal CI.

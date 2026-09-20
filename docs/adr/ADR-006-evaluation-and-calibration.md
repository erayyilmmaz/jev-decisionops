# ADR-006 — Evaluation and calibration semantics

**Status:** Accepted
**Date:** 2026-09-20

## Context

`Noul` probability, `Choice`/`Score` distributions, and provider confidence
have different meanings. Collapsing them creates invalid calibration claims.

## Decision

Score each primitive against declared ground truth using the matrix in the
technical baseline. Use the probability/distribution for Brier, log loss, and
ECE; retain confidence as separate descriptive metadata. Report provider
failures outside quality denominators. Compare runs only when compatible
contract/dataset fingerprints and declared comparison rules allow it.

## Consequences

Reports are reproducible and make denominator/sample-count limits visible.
Zero-case metrics are not applicable rather than NaN or a success result.

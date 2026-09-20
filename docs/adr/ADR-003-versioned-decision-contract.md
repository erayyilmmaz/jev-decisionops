# ADR-003 — Versioned Decision Contract

**Status:** Accepted
**Date:** 2026-09-20

## Context

Embedding questions and policy in ad-hoc Python makes review, replay, and
comparison difficult and risks an executable configuration surface.

## Decision

Use a declarative, versioned YAML Decision Contract. JDO-4 validates it into a
discriminated `Noul`/`Choice`/`Score` domain model before any provider call. It
rejects duplicate keys, unknown fields/types, empty questions, dangling policy
references, invalid thresholds, and executable code. Its canonical JSON
representation has a SHA-256 fingerprint.

## Consequences

The same semantic contract has the same fingerprint across YAML formatting
changes. Contract evolution requires an explicit version/migration decision;
arbitrary Python expressions are out of scope.

# Architecture Decision Records

These ADRs are accepted by JDO-2 and are the normative architecture baseline
for the V0 implementation.

| ADR | Decision |
| --- | --- |
| [ADR-001](ADR-001-jev-decisionops-boundary.md) | Keep Jev DecisionOps a reliability layer, not a workflow runtime. |
| [ADR-002](ADR-002-python-and-reproducible-dependencies.md) | Use CPython, `uv`, and exact provider pins. |
| [ADR-003](ADR-003-versioned-decision-contract.md) | Use a safe, versioned declarative contract. |
| [ADR-004](ADR-004-provider-abstraction.md) | Keep provider-specific SDKs outside the core domain. |
| [ADR-005](ADR-005-policy-and-abstention.md) | Apply deterministic ordered policy and explicit abstention. |
| [ADR-006](ADR-006-evaluation-and-calibration.md) | Preserve primitive-specific probability semantics. |
| [ADR-007](ADR-007-audit-ready-persistence.md) | Retain minimal immutable, reproducible run records. |
| [ADR-008](ADR-008-security-and-data-boundaries.md) | Default to data minimization and secret-safe operations. |

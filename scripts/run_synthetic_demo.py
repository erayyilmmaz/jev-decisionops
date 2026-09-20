"""Create deterministic, secretless V0 replay artifacts for a portfolio or CI demo."""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
from pathlib import Path
from uuid import UUID

from decisionops.contracts import load_contract
from decisionops.contracts.yaml_loader import load_yaml
from decisionops.evaluation import (
    RegressionEngine,
    ReplayEngine,
    regression_comparison_json,
    replay_artifact_json,
    validate_dataset_data,
)
from decisionops.models import (
    ChoiceAnswer,
    JsonValue,
    NoulAnswer,
    ProviderFailure,
    ProviderMetadata,
    ProviderResult,
    RegressionThresholds,
    ScoreAnswer,
)
from decisionops.providers import ProviderRequest

ROOT = Path(__file__).parents[1]
CONTRACT_PATH = ROOT / "contracts" / "support-ticket-triage.yaml"
DATASET_PATH = ROOT / "tests" / "fixtures" / "datasets" / "support-ticket-triage-v1.yaml"


class FixedIdentifiers:
    def __init__(self, evaluation_id: UUID) -> None:
        self._evaluation_id = evaluation_id

    def new(self) -> UUID:
        return self._evaluation_id


class SyntheticJevProvider:
    """Offline typed fake of Jev; it has neither credentials nor network access."""

    def __init__(self, *, incorrect_case_marker: str | None = None) -> None:
        self._incorrect_case_marker = incorrect_case_marker

    async def evaluate(self, request: ProviderRequest) -> ProviderResult | ProviderFailure:
        message = request.state.get("message")
        if not isinstance(message, str):
            raise ValueError("synthetic fixture message is required")
        intent, noul, urgency = _expected_answer(message)
        if self._incorrect_case_marker is not None and self._incorrect_case_marker in message:
            intent = "support"
        return ProviderResult(
            metadata=ProviderMetadata(
                provider="synthetic_jev",
                requested_model="synthetic-jev-v0",
                resolved_model="synthetic-jev-v0",
            ),
            latency_ms=12,
            answers=(
                ChoiceAnswer(
                    question_id="intent",
                    choice=intent,
                    probabilities={
                        option: 0.97 if option == intent else 0.01
                        for option in ("refund", "cancellation", "support", "information")
                    },
                    confidence=0.99,
                ),
                NoulAnswer(question_id="unauthorized_activity", noul=noul),
                ScoreAnswer(
                    question_id="urgency",
                    score=float(urgency),
                    legend={0: "low", 1: "medium", 2: "high", 3: "critical"},
                    probabilities={score: 0.97 if score == urgency else 0.01 for score in range(4)},
                    confidence=0.99,
                ),
            ),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the secretless Jev DecisionOps V0 demo")
    parser.add_argument("--repeat", type=int, default=1, help="repeat the four-case fixture")
    parser.add_argument("--output", type=Path, required=True, help="artifact output directory")
    args = parser.parse_args()
    if args.repeat < 1 or args.repeat > 1_000:
        parser.error("--repeat must be within [1, 1000]")

    contract = load_contract(CONTRACT_PATH)
    dataset = validate_dataset_data(_repeated_dataset(args.repeat), contract)
    baseline = asyncio.run(
        ReplayEngine(
            identifiers=FixedIdentifiers(UUID("00000000-0000-0000-0000-000000000501"))
        ).run(
            provider=SyntheticJevProvider(),
            contract=contract,
            dataset=dataset,
        )
    )
    candidate = asyncio.run(
        ReplayEngine(
            identifiers=FixedIdentifiers(UUID("00000000-0000-0000-0000-000000000502"))
        ).run(
            provider=SyntheticJevProvider(incorrect_case_marker="cancel my subscription"),
            contract=contract,
            dataset=dataset,
        )
    )
    comparison = RegressionEngine().compare(
        baseline,
        candidate,
        thresholds=RegressionThresholds(minimum_accuracy=1.0),
    )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "baseline-replay.json").write_text(
        replay_artifact_json(baseline), encoding="utf-8"
    )
    (args.output / "candidate-replay.json").write_text(
        replay_artifact_json(candidate), encoding="utf-8"
    )
    (args.output / "comparison.json").write_text(
        regression_comparison_json(comparison), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "baseline_evaluation_id": str(baseline.evaluation_id),
                "candidate_evaluation_id": str(candidate.evaluation_id),
                "cases": len(dataset.dataset.cases),
                "comparison_outcome": comparison.outcome.value,
            },
            sort_keys=True,
        )
    )
    return 0


def _repeated_dataset(repeat: int) -> dict[str, object]:
    data = load_yaml(DATASET_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    source_cases = data.get("cases")
    assert isinstance(source_cases, list)
    cases: list[dict[str, JsonValue]] = []
    for batch in range(repeat):
        for source_case in source_cases:
            assert isinstance(source_case, dict)
            case = copy.deepcopy(source_case)
            case_id = case.get("case_id")
            assert isinstance(case_id, str)
            case["case_id"] = f"{case_id}-{batch:03d}"
            cases.append(case)
    data["cases"] = cases
    return data


def _expected_answer(message: str) -> tuple[str, float, int]:
    if "duplicate charge" in message:
        return "refund", 0.05, 0
    if "accessed by someone else" in message:
        return "support", 0.95, 3
    if "cancel my subscription" in message:
        return "cancellation", 0.05, 1
    if "current plan information" in message:
        return "information", 0.05, 0
    raise ValueError("unrecognized synthetic fixture message")


if __name__ == "__main__":
    raise SystemExit(main())

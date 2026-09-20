"""Small deterministic release guard for repository-owned security boundaries."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1]


def main() -> int:
    violations: list[str] = []
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    environment_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    ci_workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    if "USER decisionops" not in dockerfile:
        violations.append("Dockerfile must finish with the dedicated non-root user")
    if ".env" not in gitignore.splitlines():
        violations.append(".env must remain ignored")
    for key in ("TYPESAFE_API_KEY", "SHADOW_OPENAI_API_KEY"):
        if f"{key}=" not in environment_example:
            violations.append(f".env.example must declare an empty {key} placeholder")
        elif any(
            line.startswith(f"{key}=") and line != f"{key}="
            for line in environment_example.splitlines()
        ):
            violations.append(f".env.example must not contain a {key} value")
    if "TYPESAFE_API_KEY" in ci_workflow or "SHADOW_OPENAI_API_KEY" in ci_workflow:
        violations.append("normal CI must not reference provider credentials")

    if violations:
        for violation in violations:
            print(f"security-check: {violation}")
        return 1
    print("security-check: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

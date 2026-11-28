"""Score model readings of verified aggregate metrics on a frozen checklist."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess

from run_reviews import CLAUDE_MODEL, OLLAMA_MODELS, evidence_for_review, run_claude, run_ollama


def questions(evidence):
    rows = evidence["strategies"]
    fixed, overlay, static_cash = (rows[key] for key in ("fixed_weights", "es_overlay", "matched_static_cash"))
    stress = evidence["stress_returns"]
    selloff = stress["pandemic_selloff"]
    rebound = stress["pandemic_rebound_through_q2"]
    rates = stress["inflation_and_rates_2022"]
    return {
        "drawdown": ("Was the overlay maximum drawdown closer to zero than fixed allocation?",
                     overlay["max_drawdown"] > fixed["max_drawdown"]),
        "volatility": ("Was overlay volatility lower than fixed allocation?",
                       overlay["annualized_volatility"] < fixed["annualized_volatility"]),
        "return": ("Was overlay annualized return higher than fixed allocation?",
                   overlay["cagr"] > fixed["cagr"]),
        "budget": ("Was realized holdout daily expected shortfall at or below the stated risk budget?",
                   overlay["daily_expected_shortfall_95"] <= evidence["daily_es_budget"]),
        "rebound": ("Did the overlay return exceed the static cash diagnostic in the pandemic rebound?",
                    rebound["es_overlay"] > rebound["matched_static_cash"]),
        "cash": ("Was the average holdout cash weight higher for the overlay than static cash?",
                 overlay["average_cash_weight"] > static_cash["average_cash_weight"]),
        "selloff": ("Did the overlay lose less than the static cash diagnostic during the pandemic selloff?",
                    selloff["es_overlay"] > selloff["matched_static_cash"]),
        "rate_shock": ("Did the overlay lose less than the static cash diagnostic in the 2022 rate shock?",
                       rates["es_overlay"] > rates["matched_static_cash"]),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("results/backtest.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--claude-model", default=CLAUDE_MODEL)
    parser.add_argument("--claude-command", default="claude")
    args = parser.parse_args()
    evidence = evidence_for_review(json.loads(args.input.read_text(encoding="utf-8")))
    cases = questions(evidence)
    schema = {"type": "object", "required": list(cases), "additionalProperties": False,
              "properties": {name: {"type": "string", "enum": ["yes", "no"]} for name in cases}}
    exam = {name: query for name, (query, _) in cases.items()}
    expected = {name: "yes" if truth else "no" for name, (_, truth) in cases.items()}
    body = json.dumps({"evidence": evidence, "questions": exam}, sort_keys=True, allow_nan=False)
    prompt = (
        'Answer each question using the data. Each output value must be "yes" or "no", '
        "keyed by question ID. Negative drawdown or return closer to zero is a smaller loss. "
        "All financial metrics in the JSON are unit fractions. Output JSON only.\n" + body
    )

    def evaluate(model):
        try:
            answer = (run_claude(args.claude_command, model, prompt, schema) if model == args.claude_model
                      else run_ollama(model, prompt, schema))
            if not isinstance(answer, dict) or set(answer) != set(cases) or any(
                choice not in ("yes", "no") for choice in answer.values()
            ):
                raise ValueError("Invalid responses for checklist")
            return {"model": model, "status": "complete", "answers": answer,
                    "correct": sum(answer[key] == expected[key] for key in cases), "total": len(cases)}
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            return {"model": model, "status": "unavailable", "error_type": type(error).__name__}

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(evaluate, (args.claude_model, *OLLAMA_MODELS)))
    output = {"evidence_and_question_sha256": hashlib.sha256(body.encode()).hexdigest(),
              "questions": exam, "expected": expected, "model_runs": results}
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    return int(any(row["status"] != "complete" for row in results))


if __name__ == "__main__":
    raise SystemExit(main())

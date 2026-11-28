"""Ask independent model reviewers to critique a public aggregate replay."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
from urllib.request import Request, urlopen

CLAUDE_MODEL = "claude-haiku-5-5"
OLLAMA_MODELS = ("qwen3:4b", "granite3.3:2b")
SCHEMA = {
    "type": "object",
    "required": ["findings"],
    "additionalProperties": False,
    "properties": {
        "findings": {
            "type": "array", "minItems": 2, "maxItems": 4,
            "items": {"type": "string", "minLength": 15, "maxLength": 300},
        }
    },
}

ROLES = (
    "Evaluate causal execution, outcome and assumption caveats; prioritize any defect that could overstate risk control.",
    "Compare the risk and return trade-off including the static cash diagnostic; identify an exact missing robustness check.",
    "Check terminology and evidence boundaries for historical replay, point-in-time loss estimates and the synthetic adapter.",
)
METRICS = ("cagr", "annualized_volatility", "max_drawdown", "daily_expected_shortfall_95", "average_cash_weight")
STRATEGIES = ("fixed_weights", "es_overlay", "matched_static_cash")
STRESS_WINDOWS = ("global_financial_crisis", "pandemic_selloff", "pandemic_rebound_through_q2", "inflation_and_rates_2022")


def finite_number(value):
    if type(value) not in (float, int) or not math.isfinite(value):
        raise ValueError("Review inputs must contain finite numeric aggregates")
    return value


def iso_date(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Expected an ISO session date")
    return value


def evidence_for_review(report):
    """Project only dates and numeric portfolio aggregates before model calls."""
    holdout = report["holdout"]
    return {
        "holdout": [iso_date(holdout["first_session"]), iso_date(holdout["last_session"])],
        "strategies": {
            name: {metric: finite_number(holdout["strategies"][name][metric]) for metric in METRICS}
            for name in STRATEGIES
        },
        "stress_returns": {
            case: {name: finite_number(report["historical_stress"][case]["strategies"][name]["return"]) for name in STRATEGIES}
            for case in STRESS_WINDOWS
        },
        "daily_es_budget": finite_number(report["methodology"]["risk_daily_es_limit"]),
        "trailing_sessions": finite_number(report["methodology"]["risk_lookback_sessions"]),
        "cash_yield": finite_number(report["methodology"]["cash_annual_yield"]),
        "fee_bps": finite_number(report["methodology"]["transaction_fee_bps_per_absolute_traded_dollar"]),
    }


def parse_response(raw):
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("```") and text.endswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        raw = json.loads(text)
    return raw


def parse_findings(raw):
    if not isinstance(raw, dict) or set(raw) != {"findings"}:
        raise ValueError("Unexpected review response")
    findings = raw["findings"]
    if not isinstance(findings, list) or not 2 <= len(findings) <= 4 or not all(
        isinstance(line, str) and 15 <= len(line.strip()) <= 300 for line in findings
    ):
        raise ValueError("Review findings failed validation")
    return [line.strip() for line in findings]


def prompt(data, role):
    return (
        "Review a public engineering case study for a hypothetical SPY/IEF/GLD portfolio. "
        "This is a lagged historical Expected Shortfall overlay, not a predictive model. "
        "The policy uses completed close-to-close return history, decides before execution, "
        "and rebalances at a subsequent session open with actual simulated holdings. "
        "All JSON returns and loss estimates are unit fractions. "
        "The benchmark rebalances 60/30/10 on the same schedule. The static cash comparison "
        "fixes scale in development and makes no claim of exact exposure matching in the holdout. "
        "The backtest tests historical proxy returns. Bloomberg-style reference and historical "
        "requests use synthetic fixtures in a separate demonstrator. No market connection or trades. "
        f"{role} Give 2–4 concise findings, each under 300 characters. "
        'Return only JSON with key "findings" and an array of text strings. '
        "Distinguish risks, observed outcomes and assumptions. Avoid investment advice.\n"
        + data
    )


def run_claude(command, model, text, schema=SCHEMA):
    env = dict(os.environ)
    for key in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_OAUTH_TOKEN"):
        env.pop(key, None)
    result = subprocess.run(
        [command, "-p", "--model", model, "--tools", "", "--no-session-persistence",
         "--output-format", "json", "--json-schema", json.dumps(schema)],
        input=text, text=True, capture_output=True, env=env, timeout=150, check=True,
    )
    response = json.loads(result.stdout)
    if response.get("is_error") or model not in response.get("modelUsage", {}):
        raise ValueError("Requested Claude model was not confirmed")
    return parse_response(response.get("structured_output") or response.get("result"))


def run_ollama(model, text, schema=SCHEMA):
    payload = json.dumps({
        "model": model, "prompt": text, "stream": False, "format": schema,
        "think": False, "keep_alive": 0,
        "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 650},
    }).encode()
    request = Request("http://127.0.0.1:11434/api/generate", data=payload,
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=145) as response:
        body = json.load(response)
    if body.get("model") != model or not body.get("done") or body.get("done_reason") != "stop":
        raise ValueError("Ollama returned an incomplete review")
    return parse_response(body["response"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("results/backtest.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--claude-command", default="claude")
    parser.add_argument("--claude-model", default=CLAUDE_MODEL)
    args = parser.parse_args()
    report = json.loads(args.input.read_text(encoding="utf-8"))
    data = json.dumps(evidence_for_review(report), sort_keys=True, separators=(",", ":"), allow_nan=False)

    def review(item):
        model, role = item
        text = prompt(data, role)
        try:
            if model == args.claude_model:
                findings = run_claude(args.claude_command, model, text)
            else:
                findings = run_ollama(model, text)
            return {"model": model, "status": "complete", "role": role, "findings": parse_findings(findings)}
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            return {"model": model, "status": "unavailable", "error_type": type(error).__name__}

    with ThreadPoolExecutor(max_workers=3) as executor:
        reviews = list(executor.map(review, zip((args.claude_model, *OLLAMA_MODELS), ROLES)))
    payload = {"evidence_sha256": hashlib.sha256(data.encode()).hexdigest(), "reviews": reviews}
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return int(any(row["status"] != "complete" for row in reviews))


if __name__ == "__main__":
    raise SystemExit(main())

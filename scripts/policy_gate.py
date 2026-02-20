#!/usr/bin/env python3
"""
SAE Policy Gate — Constraint computation and enforcement engine.

Combines trader state + market context into enforceable trading constraints
using the policy matrix. Outputs gate decision with concrete limits.

Usage:
    python policy_gate.py --trader-state state.json --market-context context.json [--trade-intent intent.json] [--config config.json]
"""

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from typing import Any


DEFAULT_CONFIG = {
    "thresholds": {"low_risk": 0.3, "high_risk": 0.6},
    "narrative_exclusion_thresholds": {
        "low_trader_risk": 0.8,
        "medium_trader_risk": 0.6,
        "high_trader_risk": 0.5,
    },
    "policy_matrix": {
        "low_low": {
            "decision": "ALLOW",
            "position_pct": 100,
            "leverage_pct": 100,
            "cool_down_min": 0,
            "staged": False,
            "stage_count": 1,
            "max_trades_per_hour": 20,
        },
        "low_medium": {
            "decision": "CONSTRAIN",
            "position_pct": 75,
            "leverage_pct": 75,
            "cool_down_min": 0,
            "staged": False,
            "stage_count": 1,
            "max_trades_per_hour": 10,
        },
        "low_high": {
            "decision": "CONSTRAIN",
            "position_pct": 50,
            "leverage_pct": 50,
            "cool_down_min": 0,
            "staged": True,
            "stage_count": 2,
            "max_trades_per_hour": 5,
        },
        "medium_low": {
            "decision": "CONSTRAIN",
            "position_pct": 75,
            "leverage_pct": 75,
            "cool_down_min": 0,
            "staged": False,
            "stage_count": 1,
            "max_trades_per_hour": 10,
        },
        "medium_medium": {
            "decision": "CONSTRAIN",
            "position_pct": 50,
            "leverage_pct": 50,
            "cool_down_min": 15,
            "staged": True,
            "stage_count": 2,
            "max_trades_per_hour": 5,
        },
        "medium_high": {
            "decision": "COOL_DOWN",
            "position_pct": 25,
            "leverage_pct": 25,
            "cool_down_min": 30,
            "staged": True,
            "stage_count": 3,
            "max_trades_per_hour": 2,
        },
        "high_low": {
            "decision": "CONSTRAIN",
            "position_pct": 50,
            "leverage_pct": 50,
            "cool_down_min": 15,
            "staged": True,
            "stage_count": 2,
            "max_trades_per_hour": 5,
        },
        "high_medium": {
            "decision": "COOL_DOWN",
            "position_pct": 25,
            "leverage_pct": 25,
            "cool_down_min": 30,
            "staged": True,
            "stage_count": 3,
            "max_trades_per_hour": 2,
        },
        "high_high": {
            "decision": "BLOCK",
            "position_pct": 0,
            "leverage_pct": 0,
            "cool_down_min": 60,
            "staged": False,
            "stage_count": 0,
            "max_trades_per_hour": 0,
        },
    },
}


def load_json(path: str) -> dict:
    if path == "-":
        return json.load(sys.stdin)
    with open(path) as f:
        return json.load(f)


def classify_risk(score: float, thresholds: dict) -> str:
    """Classify a 0-1 score into low/medium/high."""
    if score < thresholds["low_risk"]:
        return "low"
    elif score < thresholds["high_risk"]:
        return "medium"
    else:
        return "high"


def check_narrative_exclusion(
    trader_risk_band: str,
    narrative_intensity: float,
    config: dict,
) -> bool:
    """Check if narrative firewall should block the trade."""
    thresholds = config.get(
        "narrative_exclusion_thresholds",
        DEFAULT_CONFIG["narrative_exclusion_thresholds"],
    )

    threshold_key = f"{trader_risk_band}_trader_risk"
    threshold = thresholds.get(threshold_key, 0.8)

    return narrative_intensity >= threshold


def compute_violations(
    trade_intent: dict | None, constraints: dict, portfolio_value_usd: float
) -> list[str]:
    """Check proposed trade against gate constraints."""
    if not trade_intent:
        return []

    violations = []

    # Leverage check
    proposed_leverage = trade_intent.get("proposed_leverage", 1)
    max_leverage_pct = constraints["leverage_pct"]
    # Assume base max leverage is the proposed leverage for percentage calc
    max_leverage = max(1, proposed_leverage * max_leverage_pct / 100)
    if proposed_leverage > max_leverage and max_leverage_pct < 100:
        violations.append(
            f"Proposed leverage {proposed_leverage}x exceeds gate maximum "
            f"{max_leverage:.0f}x ({max_leverage_pct}% of proposed)"
        )

    # Position size check
    proposed_size = trade_intent.get("proposed_size_usd", 0)
    max_position_usd = portfolio_value_usd * constraints["position_pct"] / 100
    if proposed_size > max_position_usd and constraints["position_pct"] < 100:
        violations.append(
            f"Proposed size ${proposed_size:,.0f} exceeds {constraints['position_pct']}% "
            f"position cap (${max_position_usd:,.0f})"
        )

    # Block check
    if constraints["decision"] == "BLOCK":
        violations.append("All trading is blocked under current conditions")

    return violations


def compute_gate(
    trader_state: dict,
    market_context: dict,
    trade_intent: dict | None = None,
    config: dict | None = None,
) -> dict[str, Any]:
    """Main policy gate computation."""
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    thresholds = cfg["thresholds"]
    matrix = cfg["policy_matrix"]

    # Extract scores
    trader_risk = trader_state.get("risk_escalation_probability", 0.5)
    market_risk = market_context.get("error_amplification_score", 0.5)
    narrative_intensity = market_context.get("narrative_intensity", 0.0)

    # Classify risk bands
    trader_band = classify_risk(trader_risk, thresholds)
    market_band = classify_risk(market_risk, thresholds)

    # Look up policy matrix
    matrix_key = f"{trader_band}_{market_band}"
    constraints = matrix.get(matrix_key, matrix["high_high"])

    # Check narrative exclusion
    narrative_exclusion = check_narrative_exclusion(
        trader_band, narrative_intensity, cfg
    )

    # If narrative excludes, escalate decision
    decision = constraints["decision"]
    if narrative_exclusion and decision not in ("BLOCK", "COOL_DOWN"):
        decision = "COOL_DOWN"

    # Compute cool-down expiry
    cool_down_min = constraints["cool_down_min"]
    now = datetime.now(timezone.utc)
    cool_down_expires = None
    if cool_down_min > 0:
        cool_down_expires = (now + timedelta(minutes=cool_down_min)).isoformat()

    # Policy token required for CONSTRAIN with high trader risk, or any COOL_DOWN
    policy_token_required = (
        decision == "COOL_DOWN"
        or (decision == "CONSTRAIN" and trader_band == "high")
    )

    # Check violations against trade intent
    portfolio_value = 25_000  # Default if not provided
    if trade_intent:
        portfolio_value = trade_intent.get("portfolio_value_usd", portfolio_value)

    violations = compute_violations(trade_intent, {**constraints, "decision": decision}, portfolio_value)

    # Build rationale
    rationale_parts = []

    trader_dominant = trader_state.get("dominant_pattern", "unknown")
    rationale_parts.append(
        f"Trader risk {trader_band.upper()} ({trader_risk:.2f}, "
        f"dominant: {trader_dominant})"
    )

    vol_regime = market_context.get("volatility_regime", "unknown")
    rationale_parts.append(
        f"Market risk {market_band.upper()} (volatility: {vol_regime}, "
        f"error amplification: {market_risk:.2f})"
    )

    if market_context.get("event_window"):
        rationale_parts.append(
            f"Event window active: {market_context.get('event_detail', 'unknown')}"
        )

    if narrative_exclusion:
        rationale_parts.append(
            f"Narrative firewall triggered (intensity: {narrative_intensity:.2f})"
        )

    if cool_down_min > 0:
        rationale_parts.append(f"Cool-down period: {cool_down_min} minutes")

    rationale = ". ".join(rationale_parts) + "."

    return {
        "gate_decision": decision,
        "trader_risk_band": trader_band,
        "trader_risk_score": round(trader_risk, 4),
        "market_risk_band": market_band,
        "market_risk_score": round(market_risk, 4),
        "max_position_pct": constraints["position_pct"],
        "max_leverage_pct": constraints["leverage_pct"],
        "max_trades_per_hour": constraints["max_trades_per_hour"],
        "cool_down_minutes": cool_down_min,
        "cool_down_expires": cool_down_expires,
        "staged_execution": constraints["staged"],
        "stage_count": constraints["stage_count"],
        "narrative_exclusion": narrative_exclusion,
        "narrative_intensity": round(narrative_intensity, 4),
        "policy_token_required": policy_token_required,
        "violations": violations,
        "rationale": rationale,
        "assessment_time": now.isoformat(),
    }


def main():
    parser = argparse.ArgumentParser(
        description="SAE Policy Gate — Constraint computation and enforcement"
    )
    parser.add_argument(
        "--trader-state", required=True, help="Path to trader state JSON"
    )
    parser.add_argument(
        "--market-context", required=True, help="Path to market context JSON"
    )
    parser.add_argument(
        "--trade-intent", default=None, help="Path to trade intent JSON"
    )
    parser.add_argument("--config", default=None, help="Path to config JSON")
    args = parser.parse_args()

    trader_state = load_json(args.trader_state)
    market_context = load_json(args.market_context)
    trade_intent = load_json(args.trade_intent) if args.trade_intent else None
    config = load_json(args.config) if args.config else None

    result = compute_gate(trader_state, market_context, trade_intent, config)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

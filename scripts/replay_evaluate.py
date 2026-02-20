#!/usr/bin/env python3
"""
SAE Replay Evaluator — Offline backtest of SAE decisions against historical trades.

Replays each historical trade through the SAE pipeline and computes
effectiveness metrics: tail-risk reduction, false-block rate,
liquidation prevention, lead time, regime stability.

Usage:
    python replay_evaluate.py --trades historical_trades.json [--config config.json] [--output-format json|markdown]
"""

import argparse
import json
import math
import statistics
import sys
from datetime import datetime, timezone, timedelta
from typing import Any

# Import sibling modules
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trader_state import score_trader_state, filter_window, DEFAULT_CONFIG as TRADER_DEFAULT
from market_context import assess_market_context, DEFAULT_CONFIG as MARKET_DEFAULT
from policy_gate import compute_gate, DEFAULT_CONFIG as GATE_DEFAULT


def load_json(path: str) -> Any:
    if path == "-":
        return json.load(sys.stdin)
    with open(path) as f:
        return json.load(f)


def parse_timestamp(ts: str) -> datetime:
    ts = ts.replace("Z", "+00:00")
    return datetime.fromisoformat(ts)


def compute_drawdown_series(pnl_series: list[float]) -> list[float]:
    """Compute drawdown series from cumulative PnL."""
    if not pnl_series:
        return []

    cumulative = []
    total = 0.0
    for pnl in pnl_series:
        total += pnl
        cumulative.append(total)

    peak = cumulative[0]
    drawdowns = []
    for val in cumulative:
        peak = max(peak, val)
        dd = peak - val
        drawdowns.append(dd)

    return drawdowns


def compute_cvar(pnl_series: list[float], percentile: float = 5.0) -> float:
    """Compute Conditional Value at Risk (expected loss beyond VaR)."""
    if not pnl_series:
        return 0.0

    sorted_pnl = sorted(pnl_series)
    cutoff_idx = max(1, int(len(sorted_pnl) * percentile / 100))
    tail_losses = sorted_pnl[:cutoff_idx]

    return statistics.mean(tail_losses) if tail_losses else 0.0


def detect_blowup_events(
    trades: list[dict], threshold_pct: float = 20.0, portfolio_value: float = 25000.0
) -> list[dict]:
    """Detect blow-up events where cumulative loss exceeds threshold."""
    events = []
    running_loss = 0.0
    loss_start_idx = None

    for i, trade in enumerate(trades):
        pnl = trade.get("pnl_usd", 0)
        if pnl < 0:
            if loss_start_idx is None:
                loss_start_idx = i
            running_loss += pnl
            loss_pct = abs(running_loss) / portfolio_value * 100
            if loss_pct >= threshold_pct:
                events.append({
                    "start_index": loss_start_idx,
                    "end_index": i,
                    "cumulative_loss_usd": running_loss,
                    "loss_pct": round(loss_pct, 2),
                    "trade_count": i - loss_start_idx + 1,
                    "timestamp": trade.get("timestamp", ""),
                })
                running_loss = 0.0
                loss_start_idx = None
        else:
            running_loss = max(0.0, running_loss + pnl)
            if running_loss >= 0:
                running_loss = 0.0
                loss_start_idx = None

    return events


def simulate_sae_decisions(
    trades: list[dict],
    market_snapshots: list[dict] | None,
    config: dict,
) -> list[dict]:
    """Simulate SAE decisions for each trade using rolling history."""
    decisions = []

    for i, trade in enumerate(trades):
        # Build trade history up to this point (lookback window)
        history = trades[:i]

        # Score trader state from history
        trader_state = score_trader_state(history, config.get("trader_state", {}))

        # Use provided market snapshot or create minimal one
        if market_snapshots and i < len(market_snapshots):
            market_data = market_snapshots[i]
        else:
            market_data = {"asset": trade.get("asset", "unknown"), "candles_1h": []}

        market_context = assess_market_context(
            market_data, None, config.get("market_context", {})
        )

        # Compute gate decision
        trade_intent = {
            "asset": trade.get("asset"),
            "direction": trade.get("direction"),
            "proposed_size_usd": abs(trade.get("size_usd", 0)),
            "proposed_leverage": trade.get("leverage", 1),
        }

        gate = compute_gate(
            trader_state, market_context, trade_intent, config.get("policy_gate", {})
        )

        decisions.append({
            "trade_index": i,
            "timestamp": trade.get("timestamp", ""),
            "asset": trade.get("asset"),
            "direction": trade.get("direction"),
            "size_usd": trade.get("size_usd"),
            "pnl_usd": trade.get("pnl_usd", 0),
            "gate_decision": gate["gate_decision"],
            "trader_risk": gate["trader_risk_score"],
            "market_risk": gate["market_risk_score"],
            "would_block": gate["gate_decision"] in ("BLOCK", "COOL_DOWN"),
            "would_constrain": gate["gate_decision"] == "CONSTRAIN",
            "position_cap_pct": gate["max_position_pct"],
        })

    return decisions


def compute_metrics(
    trades: list[dict],
    decisions: list[dict],
    portfolio_value: float = 25000.0,
    blowup_threshold_pct: float = 20.0,
) -> dict[str, Any]:
    """Compute all evaluation metrics."""
    if not trades or not decisions:
        return {"error": "No trades to evaluate"}

    # --- Baseline (no SAE) ---
    baseline_pnl = [t.get("pnl_usd", 0) for t in trades]
    baseline_drawdowns = compute_drawdown_series(baseline_pnl)
    baseline_max_dd = max(baseline_drawdowns) if baseline_drawdowns else 0
    baseline_cvar = compute_cvar(baseline_pnl)
    baseline_blowups = detect_blowup_events(trades, blowup_threshold_pct, portfolio_value)
    baseline_total_pnl = sum(baseline_pnl)

    # --- With SAE ---
    sae_pnl = []
    blocked_trades = []
    constrained_trades = []
    allowed_trades = []

    for d in decisions:
        pnl = d["pnl_usd"]
        if d["would_block"]:
            sae_pnl.append(0)  # Blocked trade = no PnL
            blocked_trades.append(d)
        elif d["would_constrain"]:
            # Constrained: scale PnL by position cap
            scale = d["position_cap_pct"] / 100
            sae_pnl.append(pnl * scale)
            constrained_trades.append(d)
        else:
            sae_pnl.append(pnl)
            allowed_trades.append(d)

    sae_drawdowns = compute_drawdown_series(sae_pnl)
    sae_max_dd = max(sae_drawdowns) if sae_drawdowns else 0
    sae_cvar = compute_cvar(sae_pnl)
    sae_total_pnl = sum(sae_pnl)

    # --- Tail-risk reduction ---
    dd_reduction = 0.0
    if baseline_max_dd > 0:
        dd_reduction = (baseline_max_dd - sae_max_dd) / baseline_max_dd

    cvar_improvement = 0.0
    if baseline_cvar < 0:
        cvar_improvement = (sae_cvar - baseline_cvar) / abs(baseline_cvar)

    # --- False block rate ---
    false_blocks = sum(
        1 for d in blocked_trades if d["pnl_usd"] > 0
    )
    false_block_rate = (
        false_blocks / len(blocked_trades) if blocked_trades else 0.0
    )

    # --- Correct blocks (prevented losses) ---
    correct_blocks = sum(
        1 for d in blocked_trades if d["pnl_usd"] <= 0
    )
    prevented_loss = sum(
        abs(d["pnl_usd"]) for d in blocked_trades if d["pnl_usd"] < 0
    )
    missed_profit = sum(
        d["pnl_usd"] for d in blocked_trades if d["pnl_usd"] > 0
    )

    # --- Lead time ---
    lead_times = []
    for blowup in baseline_blowups:
        start_idx = blowup["start_index"]
        # Check if SAE blocked any trade before the blowup
        for d in decisions:
            if d["trade_index"] < start_idx and d["would_block"]:
                lead_trades = start_idx - d["trade_index"]
                lead_times.append(lead_trades)
                break

    avg_lead_time_trades = (
        statistics.mean(lead_times) if lead_times else 0
    )

    # --- Liquidation prevention ---
    # Check which baseline blowups SAE would have prevented
    prevented_blowups = 0
    for blowup in baseline_blowups:
        # If SAE blocked any trade in the blowup sequence
        blowup_range = range(blowup["start_index"], blowup["end_index"] + 1)
        blocked_in_range = sum(
            1 for d in decisions
            if d["trade_index"] in blowup_range and d["would_block"]
        )
        if blocked_in_range > 0:
            prevented_blowups += 1

    liquidation_prevention_rate = (
        prevented_blowups / len(baseline_blowups) if baseline_blowups else 1.0
    )

    # --- PnL impact ---
    pnl_difference = sae_total_pnl - baseline_total_pnl

    return {
        "summary": {
            "total_trades": len(trades),
            "blocked_count": len(blocked_trades),
            "constrained_count": len(constrained_trades),
            "allowed_count": len(allowed_trades),
            "blocked_pct": round(len(blocked_trades) / max(len(trades), 1) * 100, 1),
        },
        "tail_risk": {
            "baseline_max_drawdown": round(baseline_max_dd, 2),
            "sae_max_drawdown": round(sae_max_dd, 2),
            "drawdown_reduction_pct": round(dd_reduction * 100, 1),
            "baseline_cvar_5pct": round(baseline_cvar, 2),
            "sae_cvar_5pct": round(sae_cvar, 2),
            "cvar_improvement_pct": round(cvar_improvement * 100, 1),
        },
        "blowup_prevention": {
            "baseline_blowup_count": len(baseline_blowups),
            "prevented_blowups": prevented_blowups,
            "liquidation_prevention_rate": round(liquidation_prevention_rate, 4),
        },
        "accuracy": {
            "false_block_rate": round(false_block_rate, 4),
            "false_block_count": false_blocks,
            "correct_block_count": correct_blocks,
            "prevented_loss_usd": round(prevented_loss, 2),
            "missed_profit_usd": round(missed_profit, 2),
            "net_value_of_blocks": round(prevented_loss - missed_profit, 2),
        },
        "lead_time": {
            "avg_lead_time_trades": round(avg_lead_time_trades, 1),
            "blowups_with_lead_time": len(lead_times),
        },
        "pnl_impact": {
            "baseline_total_pnl": round(baseline_total_pnl, 2),
            "sae_total_pnl": round(sae_total_pnl, 2),
            "pnl_difference": round(pnl_difference, 2),
        },
        "evaluation_time": datetime.now(timezone.utc).isoformat(),
    }


def format_markdown(metrics: dict) -> str:
    """Format metrics as a markdown report."""
    lines = ["# SAE Replay Evaluation Report", ""]

    s = metrics["summary"]
    lines.append("## Summary")
    lines.append(f"- **Total trades:** {s['total_trades']}")
    lines.append(f"- **Blocked:** {s['blocked_count']} ({s['blocked_pct']}%)")
    lines.append(f"- **Constrained:** {s['constrained_count']}")
    lines.append(f"- **Allowed:** {s['allowed_count']}")
    lines.append("")

    t = metrics["tail_risk"]
    lines.append("## Tail Risk Reduction")
    lines.append(f"| Metric | Baseline | With SAE | Improvement |")
    lines.append(f"|---|---|---|---|")
    lines.append(
        f"| Max Drawdown | ${t['baseline_max_drawdown']:,.2f} | "
        f"${t['sae_max_drawdown']:,.2f} | {t['drawdown_reduction_pct']}% |"
    )
    lines.append(
        f"| CVaR (5%) | ${t['baseline_cvar_5pct']:,.2f} | "
        f"${t['sae_cvar_5pct']:,.2f} | {t['cvar_improvement_pct']}% |"
    )
    lines.append("")

    b = metrics["blowup_prevention"]
    lines.append("## Blow-Up Prevention")
    lines.append(f"- **Baseline blow-ups:** {b['baseline_blowup_count']}")
    lines.append(f"- **Prevented:** {b['prevented_blowups']}")
    lines.append(f"- **Prevention rate:** {b['liquidation_prevention_rate']*100:.1f}%")
    lines.append("")

    a = metrics["accuracy"]
    lines.append("## Block Accuracy")
    lines.append(f"- **False block rate:** {a['false_block_rate']*100:.1f}%")
    lines.append(f"- **Correct blocks:** {a['correct_block_count']}")
    lines.append(f"- **Prevented loss:** ${a['prevented_loss_usd']:,.2f}")
    lines.append(f"- **Missed profit:** ${a['missed_profit_usd']:,.2f}")
    lines.append(f"- **Net value of blocks:** ${a['net_value_of_blocks']:,.2f}")
    lines.append("")

    l = metrics["lead_time"]
    lines.append("## Lead Time")
    lines.append(f"- **Avg lead time:** {l['avg_lead_time_trades']} trades before blow-up")
    lines.append("")

    p = metrics["pnl_impact"]
    lines.append("## PnL Impact")
    lines.append(f"| Scenario | Total PnL |")
    lines.append(f"|---|---|")
    lines.append(f"| Baseline (no SAE) | ${p['baseline_total_pnl']:,.2f} |")
    lines.append(f"| With SAE | ${p['sae_total_pnl']:,.2f} |")
    lines.append(f"| Difference | ${p['pnl_difference']:,.2f} |")
    lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="SAE Replay Evaluator — Backtest SAE decisions"
    )
    parser.add_argument(
        "--trades", required=True, help="Path to historical trades JSON"
    )
    parser.add_argument(
        "--market-snapshots",
        default=None,
        help="Path to market snapshots JSON (one per trade)",
    )
    parser.add_argument("--config", default=None, help="Path to config JSON")
    parser.add_argument(
        "--portfolio-value",
        type=float,
        default=25000.0,
        help="Portfolio value in USD for percentage calculations",
    )
    parser.add_argument(
        "--blowup-threshold",
        type=float,
        default=20.0,
        help="Blow-up threshold as percentage of portfolio",
    )
    parser.add_argument(
        "--output-format",
        choices=["json", "markdown"],
        default="json",
        help="Output format",
    )
    args = parser.parse_args()

    trades = load_json(args.trades)
    market_snapshots = load_json(args.market_snapshots) if args.market_snapshots else None
    config = load_json(args.config) if args.config else {}

    # Simulate SAE decisions
    decisions = simulate_sae_decisions(trades, market_snapshots, config)

    # Compute metrics
    metrics = compute_metrics(
        trades, decisions, args.portfolio_value, args.blowup_threshold
    )

    if args.output_format == "markdown":
        print(format_markdown(metrics))
    else:
        print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()

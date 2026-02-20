#!/usr/bin/env python3
"""
SAE Trader State Model — Behavioral pattern scoring engine.

Scores 6 behavioral patterns from trade history to estimate
risk escalation probability. Python stdlib only.

Usage:
    python trader_state.py --trades trades.json [--config config.yaml] [--mode score|detail]
    cat trades.json | python trader_state.py --trades -
"""

import argparse
import json
import sys
import statistics
from datetime import datetime, timezone, timedelta
from typing import Any


# --- Default configuration ---

DEFAULT_CONFIG = {
    "weights": {
        "revenge_trading": 0.25,
        "overconfidence": 0.15,
        "high_freq_switching": 0.15,
        "late_night_impulsivity": 0.20,
        "tilt_averaging": 0.15,
        "fomo_chasing": 0.10,
    },
    "normal_trading_hours": {"start": "08:00", "end": "22:00", "timezone": "UTC"},
    "lookback_window_hours": 24,
    "revenge_reentry_minutes": 15,
    "revenge_size_ratio": 1.0,
    "overconfidence_win_streak": 4,
    "overconfidence_size_growth": 1.5,
    "high_freq_switch_threshold_per_hour": 8,
    "tilt_min_adds": 2,
    "fomo_move_pct": 5.0,
    "fomo_window_hours": 4,
}


def parse_timestamp(ts: str) -> datetime:
    """Parse ISO 8601 timestamp string to datetime."""
    ts = ts.replace("Z", "+00:00")
    return datetime.fromisoformat(ts)


def load_trades(source: str) -> list[dict]:
    """Load trades from file path or stdin."""
    if source == "-":
        data = json.load(sys.stdin)
    else:
        with open(source) as f:
            data = json.load(f)

    for t in data:
        t["_ts"] = parse_timestamp(t["timestamp"])
    data.sort(key=lambda t: t["_ts"])
    return data


def ensure_timestamps(trades: list[dict]) -> list[dict]:
    """Ensure all trades have parsed _ts field."""
    for t in trades:
        if "_ts" not in t:
            t["_ts"] = parse_timestamp(t["timestamp"])
    trades.sort(key=lambda t: t["_ts"])
    return trades


def filter_window(trades: list[dict], window_hours: int) -> list[dict]:
    """Filter trades to the lookback window."""
    if not trades:
        return []
    cutoff = trades[-1]["_ts"] - timedelta(hours=window_hours)
    return [t for t in trades if t["_ts"] >= cutoff]


# --- Pattern scoring functions ---


def score_revenge_trading(
    trades: list[dict], reentry_minutes: float, size_ratio: float
) -> float:
    """Detect loss followed by rapid re-entry with same or larger size."""
    if len(trades) < 2:
        return 0.0

    revenge_events = 0
    opportunities = 0

    for i in range(len(trades) - 1):
        curr = trades[i]
        if curr.get("pnl_usd", 0) >= 0:
            continue

        opportunities += 1
        nxt = trades[i + 1]
        gap_minutes = (nxt["_ts"] - curr["_ts"]).total_seconds() / 60

        if gap_minutes <= reentry_minutes:
            curr_size = abs(curr.get("size_usd", 0))
            next_size = abs(nxt.get("size_usd", 0))
            if curr_size > 0 and next_size / curr_size >= size_ratio:
                revenge_events += 1

    if opportunities == 0:
        return 0.0
    return min(1.0, revenge_events / max(opportunities, 1))


def score_overconfidence(
    trades: list[dict], win_streak_threshold: int, size_growth: float
) -> float:
    """Detect win streaks followed by size escalation."""
    if len(trades) < win_streak_threshold + 1:
        return 0.0

    streak = 0
    streak_start_size = 0.0
    escalation_events = 0
    total_streaks = 0

    for t in trades:
        pnl = t.get("pnl_usd", 0)
        size = abs(t.get("size_usd", 0))

        if pnl > 0:
            if streak == 0:
                streak_start_size = size
            streak += 1
        else:
            if streak >= win_streak_threshold:
                total_streaks += 1
                if streak_start_size > 0 and size / streak_start_size >= size_growth:
                    escalation_events += 1
            streak = 0

    # Check final streak
    if streak >= win_streak_threshold:
        total_streaks += 1

    if total_streaks == 0:
        return 0.0
    return min(1.0, escalation_events / max(total_streaks, 1))


def score_high_freq_switching(
    trades: list[dict], threshold_per_hour: float
) -> float:
    """Count direction and asset switches within rolling windows."""
    if len(trades) < 3:
        return 0.0

    switches = 0
    for i in range(1, len(trades)):
        prev, curr = trades[i - 1], trades[i]
        if prev.get("direction") != curr.get("direction"):
            switches += 1
        if prev.get("asset") != curr.get("asset"):
            switches += 1

    if len(trades) < 2:
        return 0.0

    span_hours = max(
        (trades[-1]["_ts"] - trades[0]["_ts"]).total_seconds() / 3600, 0.5
    )
    rate = switches / span_hours

    return min(1.0, rate / threshold_per_hour)


def score_late_night_impulsivity(
    trades: list[dict], start_hour: int, start_min: int, end_hour: int, end_min: int
) -> float:
    """Score trades outside normal trading hours."""
    if not trades:
        return 0.0

    outside_count = 0
    normal_start = start_hour * 60 + start_min
    normal_end = end_hour * 60 + end_min

    for t in trades:
        trade_min = t["_ts"].hour * 60 + t["_ts"].minute
        if normal_start <= normal_end:
            is_normal = normal_start <= trade_min <= normal_end
        else:
            is_normal = trade_min >= normal_start or trade_min <= normal_end

        if not is_normal:
            outside_count += 1

    return outside_count / len(trades)


def score_tilt_averaging(trades: list[dict], min_adds: int) -> float:
    """Detect adding to losing positions repeatedly."""
    if len(trades) < min_adds + 1:
        return 0.0

    # Group consecutive trades by (asset, direction) with negative running PnL
    tilt_sequences = 0
    total_sequences = 0
    current_asset = None
    current_direction = None
    running_pnl = 0.0
    adds_while_losing = 0

    for t in trades:
        asset = t.get("asset")
        direction = t.get("direction")
        pnl = t.get("pnl_usd", 0)

        if asset == current_asset and direction == current_direction:
            running_pnl += pnl
            if running_pnl < 0:
                adds_while_losing += 1
        else:
            if adds_while_losing >= min_adds:
                tilt_sequences += 1
            if current_asset is not None:
                total_sequences += 1
            current_asset = asset
            current_direction = direction
            running_pnl = pnl
            adds_while_losing = 0

    # Final sequence
    if adds_while_losing >= min_adds:
        tilt_sequences += 1
    if current_asset is not None:
        total_sequences += 1

    if total_sequences == 0:
        return 0.0
    return min(1.0, tilt_sequences / max(total_sequences, 1))


def score_fomo_chasing(
    trades: list[dict], move_pct: float, window_hours: float
) -> float:
    """Detect entering after large recent price moves.

    Since we only have trade-level data, approximate by checking if the user
    enters in the same direction as recent trades that showed large PnL,
    suggesting they are chasing momentum.
    """
    if len(trades) < 2:
        return 0.0

    fomo_count = 0
    for i in range(1, len(trades)):
        curr = trades[i]
        window_start = curr["_ts"] - timedelta(hours=window_hours)

        recent = [
            t
            for t in trades[:i]
            if t["_ts"] >= window_start and t.get("asset") == curr.get("asset")
        ]
        if not recent:
            continue

        total_recent_pnl = sum(t.get("pnl_usd", 0) for t in recent)
        avg_recent_size = statistics.mean(
            abs(t.get("size_usd", 1)) for t in recent
        )
        if avg_recent_size == 0:
            continue

        recent_return_pct = (total_recent_pnl / avg_recent_size) * 100

        # Chasing = entering in the direction of recent large gains
        if recent_return_pct > move_pct and curr.get("direction") == recent[-1].get(
            "direction"
        ):
            fomo_count += 1
        elif recent_return_pct < -move_pct and curr.get("direction") != recent[
            -1
        ].get("direction"):
            # Contrarian on big loss = also FOMO (panic reversal)
            fomo_count += 1

    return min(1.0, fomo_count / max(len(trades) - 1, 1))


# --- Main scoring pipeline ---


def score_trader_state(trades: list[dict], config: dict) -> dict[str, Any]:
    """Run all pattern scorers and compute composite risk."""
    cfg = {**DEFAULT_CONFIG, **config}
    weights = cfg["weights"]

    trades = ensure_timestamps(trades)
    window = filter_window(trades, cfg["lookback_window_hours"])

    # Parse normal hours
    start_parts = cfg["normal_trading_hours"]["start"].split(":")
    end_parts = cfg["normal_trading_hours"]["end"].split(":")
    start_h, start_m = int(start_parts[0]), int(start_parts[1])
    end_h, end_m = int(end_parts[0]), int(end_parts[1])

    patterns = {
        "revenge_trading": score_revenge_trading(
            window, cfg["revenge_reentry_minutes"], cfg["revenge_size_ratio"]
        ),
        "overconfidence": score_overconfidence(
            window,
            cfg["overconfidence_win_streak"],
            cfg["overconfidence_size_growth"],
        ),
        "high_freq_switching": score_high_freq_switching(
            window, cfg["high_freq_switch_threshold_per_hour"]
        ),
        "late_night_impulsivity": score_late_night_impulsivity(
            window, start_h, start_m, end_h, end_m
        ),
        "tilt_averaging": score_tilt_averaging(window, cfg["tilt_min_adds"]),
        "fomo_chasing": score_fomo_chasing(
            window, cfg["fomo_move_pct"], cfg["fomo_window_hours"]
        ),
    }

    # Weighted composite
    total_weight = sum(weights.values())
    composite = sum(
        patterns[p] * weights.get(p, 0) for p in patterns
    ) / max(total_weight, 0.01)
    composite = min(1.0, max(0.0, composite))

    dominant = max(patterns, key=patterns.get)

    return {
        "risk_escalation_probability": round(composite, 4),
        "patterns": {k: round(v, 4) for k, v in patterns.items()},
        "dominant_pattern": dominant,
        "window_analyzed": f"{cfg['lookback_window_hours']}h",
        "trade_count": len(window),
        "assessment_time": datetime.now(timezone.utc).isoformat(),
    }


def load_config(path: str | None) -> dict:
    """Load YAML-like config. Supports JSON for stdlib-only operation."""
    if path is None:
        return {}
    with open(path) as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(
        description="SAE Trader State Model — Behavioral pattern scoring"
    )
    parser.add_argument(
        "--trades",
        required=True,
        help='Path to trades JSON file, or "-" for stdin',
    )
    parser.add_argument("--config", default=None, help="Path to config JSON file")
    parser.add_argument(
        "--mode",
        choices=["score", "detail"],
        default="score",
        help="Output mode: score (compact) or detail (full)",
    )
    args = parser.parse_args()

    trades = load_trades(args.trades)
    config = load_config(args.config)
    result = score_trader_state(trades, config)

    if args.mode == "score":
        compact = {
            "risk_escalation_probability": result["risk_escalation_probability"],
            "dominant_pattern": result["dominant_pattern"],
            "trade_count": result["trade_count"],
            "patterns": result["patterns"],
        }
        print(json.dumps(compact, indent=2))
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

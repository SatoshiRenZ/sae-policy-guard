#!/usr/bin/env python3
"""
SAE Market/Narrative Context — Environment assessment engine.

Computes volatility regime, liquidity score, event windows,
narrative intensity, and error amplification score.

Usage:
    python market_context.py --market market.json [--sentiment sentiment.json] [--events events.json] [--config config.json] [--mode assess|narrative]
"""

import argparse
import json
import math
import statistics
import sys
from datetime import datetime, timezone, timedelta
from typing import Any


DEFAULT_CONFIG = {
    "volatility_percentiles": {"low": 25, "elevated": 75, "extreme": 90},
    "liquidity_depth_baseline_usd": 5_000_000,
    "spread_baseline_bps": 5.0,
    "narrative_intensity_multiplier": 3.0,
    "event_lookahead_hours": 24,
    "error_amplification_weights": {
        "volatility": 0.30,
        "liquidity_inverse": 0.25,
        "event_window": 0.15,
        "narrative": 0.30,
    },
    "events": [],
}


def parse_timestamp(ts: str) -> datetime:
    ts = ts.replace("Z", "+00:00")
    return datetime.fromisoformat(ts)


def load_json(path: str) -> dict:
    if path == "-":
        return json.load(sys.stdin)
    with open(path) as f:
        return json.load(f)


# --- Volatility ---


def compute_returns(candles: list[dict]) -> list[float]:
    """Compute log returns from candle close prices."""
    returns = []
    for i in range(1, len(candles)):
        prev_close = candles[i - 1]["close"]
        curr_close = candles[i]["close"]
        if prev_close > 0 and curr_close > 0:
            returns.append(math.log(curr_close / prev_close))
    return returns


def compute_atr(candles: list[dict], period: int = 14) -> float:
    """Compute Average True Range from candles."""
    if len(candles) < 2:
        return 0.0

    true_ranges = []
    for i in range(1, len(candles)):
        c = candles[i]
        prev_close = candles[i - 1]["close"]
        tr = max(
            c["high"] - c["low"],
            abs(c["high"] - prev_close),
            abs(c["low"] - prev_close),
        )
        true_ranges.append(tr)

    if not true_ranges:
        return 0.0

    # Simple moving average ATR
    window = true_ranges[-period:]
    return statistics.mean(window)


def assess_volatility(candles: list[dict], config: dict) -> dict:
    """Compute volatility regime from 1h candles."""
    if len(candles) < 24:
        return {
            "volatility_regime": "unknown",
            "volatility_percentile": 0.5,
            "realized_vol_24h": 0.0,
            "atr_14": 0.0,
        }

    returns = compute_returns(candles)

    # 24h realized volatility (annualized from hourly)
    recent_returns = returns[-24:]
    if recent_returns:
        hourly_vol = statistics.stdev(recent_returns) if len(recent_returns) > 1 else 0
        realized_vol_24h = hourly_vol * math.sqrt(24 * 365)
    else:
        realized_vol_24h = 0

    # Compute rolling 24h volatilities for percentile ranking
    rolling_vols = []
    for i in range(24, len(returns) + 1):
        window = returns[i - 24 : i]
        if len(window) > 1:
            rolling_vols.append(statistics.stdev(window))

    if rolling_vols and len(rolling_vols) > 1:
        sorted_vols = sorted(rolling_vols)
        current_vol = rolling_vols[-1] if rolling_vols else 0
        rank = sum(1 for v in sorted_vols if v <= current_vol)
        percentile = rank / len(sorted_vols) * 100
    else:
        percentile = 50.0

    pctiles = config.get("volatility_percentiles", DEFAULT_CONFIG["volatility_percentiles"])
    if percentile < pctiles["low"]:
        regime = "low"
    elif percentile < pctiles["elevated"]:
        regime = "normal"
    elif percentile < pctiles["extreme"]:
        regime = "elevated"
    else:
        regime = "extreme"

    atr = compute_atr(candles)

    return {
        "volatility_regime": regime,
        "volatility_percentile": round(percentile / 100, 4),
        "realized_vol_24h": round(realized_vol_24h, 6),
        "atr_14": round(atr, 2),
    }


# --- Liquidity ---


def assess_liquidity(market_data: dict, config: dict) -> dict:
    """Score liquidity from order book depth and spread."""
    baseline_depth = config.get(
        "liquidity_depth_baseline_usd",
        DEFAULT_CONFIG["liquidity_depth_baseline_usd"],
    )
    baseline_spread = config.get(
        "spread_baseline_bps", DEFAULT_CONFIG["spread_baseline_bps"]
    )

    depth = market_data.get("orderbook_depth_bps_10", baseline_depth)
    spread = market_data.get("spread_bps", baseline_spread)

    # Depth score: higher depth = better liquidity
    depth_score = min(1.0, depth / baseline_depth) if baseline_depth > 0 else 0.5

    # Spread score: lower spread = better liquidity
    spread_score = min(1.0, baseline_spread / max(spread, 0.01))

    liquidity_score = 0.6 * depth_score + 0.4 * spread_score

    return {
        "liquidity_score": round(min(1.0, max(0.0, liquidity_score)), 4),
        "depth_usd": depth,
        "spread_bps": spread,
    }


# --- Event Window ---


def assess_event_window(config: dict) -> dict:
    """Check if we're within lookahead window of any known event."""
    lookahead_hours = config.get(
        "event_lookahead_hours", DEFAULT_CONFIG["event_lookahead_hours"]
    )
    events = config.get("events", DEFAULT_CONFIG["events"])
    now = datetime.now(timezone.utc)

    for event in events:
        event_time = parse_timestamp(event["timestamp"])
        delta = event_time - now
        if timedelta(0) <= delta <= timedelta(hours=lookahead_hours):
            hours_until = delta.total_seconds() / 3600
            return {
                "event_window": True,
                "event_detail": f"{event.get('name', 'Unknown event')} in {hours_until:.0f}h",
                "hours_until_event": round(hours_until, 1),
            }

    return {"event_window": False, "event_detail": None, "hours_until_event": None}


# --- Narrative ---


def assess_narrative(sentiment_data: dict | None, config: dict) -> dict:
    """Score narrative intensity from sentiment data."""
    if not sentiment_data:
        return {
            "narrative_intensity": 0.0,
            "narrative_type": "unknown",
        }

    vol_24h = sentiment_data.get("social_volume_24h", 0)
    vol_avg = sentiment_data.get("social_volume_7d_avg", 1)
    sentiment_score = sentiment_data.get("sentiment_score", 0.5)
    keywords = sentiment_data.get("top_keywords", [])

    multiplier = config.get(
        "narrative_intensity_multiplier",
        DEFAULT_CONFIG["narrative_intensity_multiplier"],
    )

    # Volume anomaly
    volume_ratio = vol_24h / max(vol_avg, 1)
    intensity = min(1.0, volume_ratio / multiplier)

    # Classify narrative type
    euphoria_keywords = {"moon", "ath", "breakout", "pump", "bullish", "generational", "parabolic"}
    panic_keywords = {"crash", "dump", "liquidation", "scam", "rug", "bearish", "collapse"}

    lower_keywords = {k.lower() for k in keywords}
    euphoria_hits = len(lower_keywords & euphoria_keywords)
    panic_hits = len(lower_keywords & panic_keywords)

    if euphoria_hits > panic_hits:
        narrative_type = "euphoria"
    elif panic_hits > euphoria_hits:
        narrative_type = "panic"
    elif intensity > 0.5:
        narrative_type = "high_attention"
    else:
        narrative_type = "neutral"

    return {
        "narrative_intensity": round(intensity, 4),
        "narrative_type": narrative_type,
        "volume_ratio": round(volume_ratio, 2),
        "sentiment_score": sentiment_score,
    }


# --- Composite ---


def compute_error_amplification(
    volatility: dict, liquidity: dict, event: dict, narrative: dict, config: dict
) -> float:
    """Weighted composite error amplification score."""
    weights = config.get(
        "error_amplification_weights",
        DEFAULT_CONFIG["error_amplification_weights"],
    )

    vol_score = volatility.get("volatility_percentile", 0.5)
    liq_inverse = 1.0 - liquidity.get("liquidity_score", 0.5)
    event_score = 1.0 if event.get("event_window") else 0.0
    narr_score = narrative.get("narrative_intensity", 0.0)

    composite = (
        weights["volatility"] * vol_score
        + weights["liquidity_inverse"] * liq_inverse
        + weights["event_window"] * event_score
        + weights["narrative"] * narr_score
    )

    return round(min(1.0, max(0.0, composite)), 4)


# --- Main ---


def assess_market_context(
    market_data: dict,
    sentiment_data: dict | None = None,
    config: dict | None = None,
) -> dict[str, Any]:
    """Full market/narrative context assessment."""
    cfg = {**DEFAULT_CONFIG, **(config or {})}

    candles = market_data.get("candles_1h", [])
    volatility = assess_volatility(candles, cfg)
    liquidity = assess_liquidity(market_data, cfg)
    event = assess_event_window(cfg)
    narrative = assess_narrative(sentiment_data, cfg)

    error_amp = compute_error_amplification(
        volatility, liquidity, event, narrative, cfg
    )

    return {
        "asset": market_data.get("asset", "unknown"),
        **volatility,
        **liquidity,
        **event,
        **narrative,
        "error_amplification_score": error_amp,
        "funding_rate": market_data.get("funding_rate"),
        "open_interest_usd": market_data.get("open_interest_usd"),
        "assessment_time": datetime.now(timezone.utc).isoformat(),
    }


def main():
    parser = argparse.ArgumentParser(
        description="SAE Market/Narrative Context Assessment"
    )
    parser.add_argument("--market", required=True, help="Path to market data JSON")
    parser.add_argument("--sentiment", default=None, help="Path to sentiment data JSON")
    parser.add_argument("--events", default=None, help="Path to events JSON")
    parser.add_argument("--config", default=None, help="Path to config JSON")
    parser.add_argument(
        "--mode",
        choices=["assess", "narrative"],
        default="assess",
        help="assess: full context; narrative: narrative firewall only",
    )
    args = parser.parse_args()

    market_data = load_json(args.market)
    sentiment_data = load_json(args.sentiment) if args.sentiment else None
    config = load_json(args.config) if args.config else {}

    if args.events:
        events_data = load_json(args.events)
        config["events"] = events_data if isinstance(events_data, list) else events_data.get("events", [])

    if args.mode == "narrative":
        result = assess_narrative(sentiment_data, {**DEFAULT_CONFIG, **config})
        print(json.dumps(result, indent=2))
    else:
        result = assess_market_context(market_data, sentiment_data, config)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

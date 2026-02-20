# Behavioral Patterns Reference

Detailed definitions, detection algorithms, and scoring for each of the six
behavioral patterns tracked by the SAE Trader State Model.

## 1. Revenge Trading

**Definition:** Immediately re-entering a position after a loss, often with equal
or larger size, driven by the desire to "win back" lost money rather than by
analysis.

**Detection Algorithm:**
- For each losing trade (pnl_usd < 0), check the next trade
- If the gap is within `revenge_reentry_minutes` (default: 15) AND
  the next trade's size >= `revenge_size_ratio` (default: 1.0) of the losing trade
- Count as a revenge event

**Score:** revenge_events / loss_opportunities (0.0 to 1.0)

**Interpretation:**
- 0.0–0.2: Normal post-loss behavior
- 0.2–0.5: Some reactive re-entry, mild concern
- 0.5–0.8: Frequent revenge patterns, elevated risk
- 0.8–1.0: Systematic revenge trading, high risk of spiral

**Mitigation:** Enforce cool-down period after losses. Minimum 15-minute delay
before next trade. Scale cool-down with loss severity.

**Example — High Score (0.85):**
```
14:00 BTC-PERP long  $5,000  PnL: -$450   (loss)
14:08 BTC-PERP long  $7,000  PnL: -$320   (revenge: 8 min gap, 1.4x size)
14:15 BTC-PERP long  $10,000 PnL: -$1,200  (revenge: 7 min gap, 1.4x size)
```

## 2. Overconfidence

**Definition:** Escalating position sizes after a streak of wins, mistaking recent
luck for skill, leading to outsized exposure when the streak inevitably breaks.

**Detection Algorithm:**
- Track consecutive winning trades (pnl_usd > 0)
- If streak >= `overconfidence_win_streak` (default: 4)
- Check if the next trade size is >= `overconfidence_size_growth` (default: 1.5x) of the streak's initial size
- Count as an overconfidence event

**Score:** escalation_events / total_win_streaks (0.0 to 1.0)

**Interpretation:**
- 0.0–0.2: Stable sizing through wins
- 0.2–0.5: Some escalation, monitor
- 0.5–0.8: Consistent size escalation after wins
- 0.8–1.0: Dangerous leverage ratcheting

**Mitigation:** Cap position growth rate. Maximum 1.25x size increase per trade
regardless of recent results.

## 3. High-Frequency Switching

**Definition:** Rapidly changing trading direction (long to short) or switching
between assets, indicating indecision, anxiety, or reactive trading without a plan.

**Detection Algorithm:**
- For each consecutive pair of trades, count direction changes and asset changes
- Compute rate: total_switches / time_span_hours
- Normalize against `high_freq_switch_threshold_per_hour` (default: 8)

**Score:** switch_rate / threshold_rate (capped at 1.0)

**Interpretation:**
- 0.0–0.3: Normal diversified trading
- 0.3–0.6: Elevated switching, possible anxiety
- 0.6–0.8: High-frequency direction flipping
- 0.8–1.0: Chaotic trading pattern, likely emotional

**Mitigation:** Rate-limit direction changes. Maximum 4 direction reversals per
hour on the same asset.

## 4. Late-Night Impulsivity

**Definition:** Trading outside one's normal active hours, often late at night,
when decision quality is degraded by fatigue, isolation, or emotional reactivity.

**Detection Algorithm:**
- Compare each trade's timestamp against configured `normal_trading_hours`
- Count trades outside the normal window
- Score = outside_count / total_count

**Score:** fraction of trades outside normal hours (0.0 to 1.0)

**Interpretation:**
- 0.0–0.1: Trading within normal hours
- 0.1–0.3: Occasional off-hours trades
- 0.3–0.6: Significant off-hours activity
- 0.6–1.0: Predominantly off-hours trading, high impulsivity risk

**Mitigation:** Reduce position limits by 50% outside normal hours. Block new
positions entirely during configured blackout hours.

**Configuration:** Adjust `normal_trading_hours` in config to match the trader's
actual schedule. Default: 08:00–22:00 UTC.

## 5. Tilt Averaging

**Definition:** Repeatedly adding to a losing position (averaging down/up against
the trend) in an attempt to lower average entry price, often resulting in
concentrated risk and eventual liquidation.

**Detection Algorithm:**
- Group consecutive trades by (asset, direction)
- Track cumulative unrealized PnL within the group
- If cumulative PnL is negative and the trader adds another position in the same direction
- Count consecutive adds-while-losing as a tilt sequence
- Require `tilt_min_adds` (default: 2) adds while losing to trigger

**Score:** tilt_sequences / total_position_sequences (0.0 to 1.0)

**Interpretation:**
- 0.0–0.2: Planned scaling (if pre-defined) or rare adds
- 0.2–0.5: Some averaging, monitor whether it's planned
- 0.5–0.8: Frequent tilt averaging, likely emotional
- 0.8–1.0: Systematic doubling-down on losers

**Mitigation:** Enforce maximum adds per position. Block same-direction trades on
the same asset when unrealized PnL exceeds -5% of position value.

## 6. FOMO Chasing

**Definition:** Entering a position after a large price move has already occurred,
chasing momentum out of fear of missing out. Typically results in buying tops or
selling bottoms.

**Detection Algorithm:**
- For each trade, look at recent trades on the same asset within `fomo_window_hours`
- If the recent cumulative return exceeds `fomo_move_pct` (default: 5%)
- And the new trade follows the momentum direction (or panic-reverses against it)
- Count as a FOMO event

**Score:** fomo_events / total_non-first_trades (0.0 to 1.0)

**Interpretation:**
- 0.0–0.2: Measured entries, not chasing
- 0.2–0.4: Some trend-following, could be intentional
- 0.4–0.7: Frequent momentum chasing
- 0.7–1.0: Systematic FOMO trading, buying tops

**Mitigation:** Enforce staged execution on entries after large moves.
Require 2-tranche execution minimum when entering after >5% move.

## Composite Risk Escalation Probability

The overall `risk_escalation_probability` is a weighted combination of all six
pattern scores:

```
composite = sum(pattern_score[i] * weight[i]) / sum(weight[i])
```

**Default weights:**
| Pattern | Weight |
|---|---|
| revenge_trading | 0.25 |
| late_night_impulsivity | 0.20 |
| overconfidence | 0.15 |
| high_freq_switching | 0.15 |
| tilt_averaging | 0.15 |
| fomo_chasing | 0.10 |

Weights reflect the relative danger of each pattern. Revenge trading and
late-night impulsivity are weighted highest because they most directly lead to
rapid account destruction.

All weights are configurable via `config-schema.yaml`.

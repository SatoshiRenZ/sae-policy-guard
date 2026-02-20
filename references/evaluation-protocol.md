# Evaluation Protocol Reference

Reproducible methodology for measuring SAE effectiveness via offline replay.

## Experiment Design

### Control vs. Treatment
- **Control (baseline):** All trades executed as-is, no SAE intervention
- **Treatment:** Each trade evaluated by SAE pipeline; BLOCK/COOL_DOWN trades
  removed, CONSTRAIN trades scaled by position cap percentage

### Ablation Studies
Test the contribution of each SAE component by removing one at a time:

1. **Full SAE:** All three modules active (trader state + market context + policy gate)
2. **No cool-down:** Remove all cool-down delays, keep position budgets and narrative firewall
3. **No narrative firewall:** Remove narrative exclusion zones, keep cool-downs and budgets
4. **Position budget only:** Remove cool-downs and narrative firewall, keep only position/leverage caps
5. **No SAE (baseline):** No intervention

## Metric Definitions

### 1. Tail-Risk Reduction

**Max Drawdown:**
```
MaxDD = max(peak_equity(t) - equity(t)) for all t
Reduction% = (MaxDD_baseline - MaxDD_sae) / MaxDD_baseline * 100
```

**CVaR (Conditional Value at Risk) at 5%:**
```
Sort all trade PnLs ascending
VaR_5% = PnL at the 5th percentile
CVaR_5% = mean(all PnLs <= VaR_5%)
Improvement% = (CVaR_sae - CVaR_baseline) / |CVaR_baseline| * 100
```

A positive improvement means SAE reduced the expected loss in the worst 5% of outcomes.

### 2. Blow-Up / Liquidation Prevention

**Blow-up definition:** A sequence of trades where cumulative loss exceeds a
threshold percentage of portfolio value (default: 20%).

```
Blow-up detected when:
  running_cumulative_loss > portfolio_value * threshold_pct / 100

Prevention rate = prevented_blowups / baseline_blowups
```

A prevention rate of 1.0 means SAE would have prevented all historical blow-ups.

### 3. Lead Time

How many trades before a blow-up event does SAE first trigger a BLOCK or COOL_DOWN?

```
For each baseline blow-up event:
  Find the earliest SAE BLOCK/COOL_DOWN decision preceding the event
  Lead time = blow-up_start_index - first_block_index

Average lead time = mean(all lead times)
```

Higher lead time = earlier warning = more effective protection.

### 4. False-Block Rate

Fraction of blocked trades that would have been profitable (opportunity cost).

```
False-block rate = profitable_blocked_trades / total_blocked_trades
```

**Interpretation:**
- 0.0–0.15: Excellent precision, minimal opportunity cost
- 0.15–0.30: Acceptable, worth the protection
- 0.30–0.50: Significant opportunity cost, consider tuning thresholds
- 0.50+: Overly aggressive blocking, lower thresholds

**Net value of blocks:**
```
Net value = prevented_losses - missed_profits
```

Positive net value means blocking was net beneficial.

### 5. Regime Robustness

Stability of SAE performance across different market conditions.

**Methodology:**
- Classify each period as bull / bear / high-volatility using 30-day return and volatility
- Compute all metrics separately for each regime
- Report coefficient of variation across regimes

```
Robustness = 1 - CV(metric across regimes)
```

A robustness of 1.0 means perfectly consistent performance across regimes.
Values > 0.7 indicate good stability.

## Data Requirements

### Trade History Format
JSON array of trade records with fields:
- `timestamp` (ISO 8601)
- `asset` (string)
- `direction` ("long" | "short")
- `size_usd` (float)
- `leverage` (float)
- `pnl_usd` (float)
- `holding_minutes` (float)
- `was_stop_loss` (boolean)

Minimum 50 trades recommended for meaningful metrics. 200+ trades preferred.

### Market Snapshots (Optional)
JSON array of market states, one per trade, with:
- `candles_1h` (168 candles = 7 days)
- `funding_rate`, `open_interest_usd`
- `orderbook_depth_bps_10`, `spread_bps`

If not provided, market context defaults to neutral (error_amplification = 0.5).

## Reporting Format

The `replay_evaluate.py` script outputs:
- **JSON mode:** Full metrics dictionary
- **Markdown mode:** Formatted report with tables

### Key Sections in Report
1. **Summary:** Trade counts by SAE decision
2. **Tail Risk:** Drawdown and CVaR comparison
3. **Blow-Up Prevention:** Liquidation count and prevention rate
4. **Block Accuracy:** False-block rate, prevented loss, missed profit
5. **Lead Time:** Average warning lead time
6. **PnL Impact:** Total PnL comparison

## Tuning Guidance

Based on evaluation results:

| Finding | Action |
|---|---|
| False-block rate > 30% | Raise risk thresholds in config (e.g., high_risk from 0.6 to 0.7) |
| Blow-ups not prevented | Lower risk thresholds or increase cool-down durations |
| Lead time < 2 trades | Increase lookback window or lower detection thresholds |
| Large PnL penalty | Check if constrain levels are too aggressive, raise position caps |
| Inconsistent across regimes | Add regime-dependent threshold adjustment |

Re-run evaluation after each config change to verify improvement.

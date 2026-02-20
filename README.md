# SAE Policy Guard

**Survivability-Aware Execution for Crypto Trading** — a skill that gates trade execution based on trader behavioral state, market/narrative context, and enforceable policy constraints.

Built on the SAE framework from the whitepaper *"Execution Is the New Attack Surface: Survivability-Aware AI for Crypto Trading."*

## What it does

Before any crypto trade is executed, SAE Policy Guard:

1. **Scores trader behavioral state** — detects revenge trading, overconfidence, tilt averaging, FOMO chasing, late-night impulsivity, and high-frequency switching
2. **Assesses market/narrative context** — evaluates volatility regime, liquidity, event windows, and narrative intensity (social media euphoria/panic)
3. **Computes a policy gate decision** — outputs one of: **ALLOW**, **CONSTRAIN**, **COOL_DOWN**, or **BLOCK** with enforceable constraints (position caps, leverage limits, cool-down timers, staged execution, narrative exclusion)

It also includes a **threat audit** scanner for trading plugins/extensions (supply-chain risk, prompt injection, data leakage) and a **replay evaluator** for backtesting SAE decisions against historical trades.

## Installation

```bash
npx skills add True-AI-Labs/sae-policy-guard
```

## Quick Reference

| Task | Command |
|---|---|
| Pre-trade risk check | Run full SAE pipeline (trader state → market context → policy gate) |
| Behavioral scoring | `python scripts/trader_state.py --trades trades.json` |
| Market assessment | `python scripts/market_context.py --market market.json` |
| Policy gate | `python scripts/policy_gate.py --trader-state state.json --market-context context.json` |
| Narrative firewall | `python scripts/market_context.py --market market.json --mode narrative` |
| Plugin security audit | `python scripts/threat_audit.py --target <path>` |
| Backtest SAE decisions | `python scripts/replay_evaluate.py --trades history.json` |

## Policy Matrix

The gate maps trader risk x market risk to enforceable constraints:

| Trader | Market | Decision | Position Cap | Leverage Cap | Cool-Down |
|---|---|---|---|---|---|
| Low | Low | ALLOW | 100% | 100% | 0 min |
| Low | High | CONSTRAIN | 50% | 50% | 0 min |
| Medium | Medium | CONSTRAIN | 50% | 50% | 15 min |
| High | Medium | COOL_DOWN | 25% | 25% | 30 min |
| High | High | **BLOCK** | 0% | 0% | 60 min |

Full 9-cell matrix in [SKILL.md](SKILL.md).

## Behavioral Patterns Detected

- **Revenge trading** — loss → rapid re-entry with increased size
- **Overconfidence** — win streak → size escalation
- **High-frequency switching** — excessive direction/asset changes
- **Late-night impulsivity** — trades outside normal hours
- **Tilt averaging** — adding to losing positions
- **FOMO chasing** — entering after large price moves

## Requirements

- Python 3.10+
- No external dependencies (Python stdlib only)
- Exchange-agnostic (accepts JSON input from any source)

## Configuration

All thresholds are tunable. Copy `assets/config-schema.yaml`, modify values, and pass with `--config` to any script.

## License

Apache 2.0 — see [LICENSE](LICENSE).

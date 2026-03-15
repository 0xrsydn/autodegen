# Swarm Executor Bug Report

**Status**: Open  
**Severity**: High — causes false rejections of passing strategies  
**Component**: `.pi/agents/executor.md`

## Problem

The swarm executor incorrectly rejects strategies that pass `hard_gates` due to parsing `status=?` instead of the `hard_gates` field from `results.tsv`.

## Evidence

During the 20-iteration run on `feat/multidegen` (2026-03-15):

| Iteration | Strategy | composite | hard_gates | Executor Verdict |
|-----------|----------|-----------|------------|------------------|
| 8 | `risk_exit_max_smoothed_atr` | 0.761293 | **PASS** | ❌ Rejected "not passed" |
| 11 | `breakout_ratio_trail_22_50` | 0.677117 | **PASS** | ❌ Reverted (later recovered) |
| 13 | `vol_adaptive_asymmetric_trail` | 0.674325 | **PASS** | ❌ Rejected "not passed" |
| 16 | `asymmetry_scout_22_50` | 0.647924 | **PASS** | ❌ Rejected "not passed" |
| 18 | `volnorm_sizing_22_50_v2` | 0.633145 | **PASS** | ❌ Rejected "not passed" |

All 5 were manually verified in `results.tsv` as `hard_gates=PASS`.

## Root Cause

The executor agent likely checks `status=?` (unknown/pending field) instead of parsing `hard_gates=PASS/FAIL` from the TSV output.

## Impact

- **Iteration 11** was the new best (0.677 > 0.643) but was auto-reverted
- Higher-composite runs (e.g., #8 at 0.761) were lost
- Required manual recovery via `git` and `strategy.py` recreation

## Proposed Fix

Update executor agent's accept/reject logic:

```python
# Current (buggy)
if row.status == "?": reject()

# Fixed
if row.hard_gates != "PASS": reject()
```

Or check both fields for safety.

## Workaround

After swarm runs, manually verify `results.tsv`:

```bash
grep "PASS" results.tsv | sort -t$'\t' -k3 -rn | head -5
```

Cross-check against "Accepted: X" in swarm output. Recover via:

```bash
git log --oneline -20  # find lost commit
git show <commit>:strategy.py > strategy.py
uv run python strategy.py  # re-validate
```

## Reference

- Session: 2026-03-15, 20 iterations, `feat/multidegen`
- Recovered commit: `f120d69` (breakout_ratio_trail_22_50, composite=0.677)
- Push: `7ad15c2` (includes swarm configs + bug report)

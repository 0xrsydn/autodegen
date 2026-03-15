# autodegen-swarm

Repo-local pi extension for this repository.

## What it adds

- `degen_swarm` tool
- `/degen-once` command
- `/degen-loop <n> [goal]` command
- `/degen-research` command
- project-local swarm agents in `.pi/agents/`
- agent frontmatter can pin both `provider:` and `model:` for deterministic routing
- model/provider choice is intentionally hackable per agent; the swarm assigns tasks by role, not by hardcoded model family

## Intended workflow

- parallel read-only agents inspect `results.tsv` and `strategy.py`
- scout families cover both simple and broader alpha archetypes:
  - trend following
  - breakout
  - mean reversion
  - regime switching
  - long/short asymmetry
  - volatility-state logic
  - structural pullback continuation
  - exhaustion / recovery
  - path-dependent risk / exit shaping
- a critic picks one hypothesis
- selection policy is adaptive:
  - if candidates are close, simpler classic families are preferred
  - after 5 failed experiments in a row, the critic is biased toward broader archetype pivots
- anti-repetition guard:
  - avoids reusing a family that dominates the recent ledger
  - also avoids picking the same family in back-to-back swarm iterations when a credible alternative exists
- an executor edits only `strategy.py`
- the extension runs:
  - `uv run python prepare.py validate`
  - `uv run python strategy.py`
- the extension accepts only if:
  - evaluation succeeded
  - latest `results.tsv` row has `status=PASS`
  - latest composite beats the previous best passing composite
- otherwise it restores `strategy.py`

## Notes

- This extension is repo-local via `.pi/extensions/`, so it only loads in this repository.
- Task assignment is stable, but model routing is configurable in `.pi/agents/*.md` frontmatter.
- You can freely retune providers/models per role without changing swarm orchestration, e.g.:
  ```yaml
  provider: google-antigravity
  model: gemini-3-flash
  ```
- Accepted improvements are committed as `hypothesis: <hypothesis>` when `commitOnAccept=true`.
- Failed runs still append to `results.tsv`; the extension does not edit the ledger manually.

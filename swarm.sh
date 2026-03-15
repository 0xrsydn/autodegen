#!/usr/bin/env bash
# autodegen swarm — git worktree lifecycle for role-based scout agents
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKTREE_BASE="$(dirname "$REPO_DIR")"
DATA_DIR="$REPO_DIR/data"
LEADERBOARD="$REPO_DIR/leaderboard.tsv"
ROLES_DIR="$REPO_DIR/roles"

usage() {
  cat <<EOF
Usage: swarm.sh <command> [args]

Commands:
  setup <name> <role>   Create a scout worktree with a role
                        Roles: explorer, optimizer, synthesizer, stress-tester
  setup-team            Create the standard 5-agent team
  cleanup [name]        Remove scout worktree(s) (all if no name given)
  status                Show active worktrees and their roles
  collect               Collect PASS strategies from all scouts into main leaderboard

Examples:
  ./swarm.sh setup-team                    # create the standard 5-agent team
  ./swarm.sh setup explorer-1 explorer     # create a single explorer
  ./swarm.sh collect                       # harvest PASS results
  ./swarm.sh cleanup                       # tear down all worktrees

Standard team:
  explorer-1    (explorer)
  explorer-2    (explorer)
  optimizer-1   (optimizer)
  synthesizer-1 (synthesizer)
  stress-test-1 (stress-tester)
EOF
}

setup_scout() {
  local name="${1:?Usage: swarm.sh setup <name> <role>}"
  local role="${2:?Usage: swarm.sh setup <name> <role>}"
  local role_file="$ROLES_DIR/${role}.md"

  if [[ ! -f "$role_file" ]]; then
    echo "ERROR: Unknown role '$role'. Available: explorer, optimizer, synthesizer, stress-tester"
    exit 1
  fi

  local wt_dir="$WORKTREE_BASE/autodegen-${name}"
  local branch="scout/${name}"

  # Clean up if leftover from previous run
  if git worktree list --porcelain | grep -q "$wt_dir"; then
    echo "  Cleaning leftover worktree: $name"
    git worktree remove "$wt_dir" --force 2>/dev/null || true
    git branch -D "$branch" 2>/dev/null || true
  fi

  # Create worktree on a throwaway branch from HEAD
  git worktree add "$wt_dir" -b "$branch" HEAD 2>/dev/null

  # Symlink shared data directory
  rm -rf "$wt_dir/data"
  ln -sfn "$DATA_DIR" "$wt_dir/data"

  # Symlink leaderboard (shared across all scouts)
  rm -f "$wt_dir/leaderboard.tsv"
  ln -sfn "$LEADERBOARD" "$wt_dir/leaderboard.tsv"

  # Copy role-specific degen.md as the scout's main instruction file
  cp "$role_file" "$wt_dir/degen.md"

  # Record role for status display
  echo "$role" > "$wt_dir/.role"

  # Fresh results.tsv
  : > "$wt_dir/results.tsv"

  # Create empty BRIEFING.md (to be filled by orchestrator)
  if [[ ! -f "$wt_dir/BRIEFING.md" ]]; then
    echo "# BRIEFING — $name ($role)" > "$wt_dir/BRIEFING.md"
    echo "" >> "$wt_dir/BRIEFING.md"
    echo "Briefing not yet written. Wait for orchestrator to fill this." >> "$wt_dir/BRIEFING.md"
  fi

  # Create empty STRESS_REPORT.md for stress testers
  if [[ "$role" == "stress-tester" ]]; then
    : > "$wt_dir/STRESS_REPORT.md"
  fi

  echo "  ✓ ${name} (${role}) ready at $wt_dir"
}

cmd_setup_team() {
  echo "Setting up standard 5-agent team..."

  setup_scout "explorer-1" "explorer"
  setup_scout "explorer-2" "explorer"
  setup_scout "optimizer-1" "optimizer"
  setup_scout "synthesizer-1" "synthesizer"
  setup_scout "stress-test-1" "stress-tester"

  echo ""
  echo "Team ready. Write BRIEFING.md for each scout before launching agents."
  echo ""
  echo "Worktree locations:"
  for d in "$WORKTREE_BASE"/autodegen-{explorer-1,explorer-2,optimizer-1,synthesizer-1,stress-test-1}; do
    if [[ -d "$d" ]]; then
      local role=$(cat "$d/.role" 2>/dev/null || echo "unknown")
      echo "  $(basename "$d") [$role]: $d"
    fi
  done
}

cmd_cleanup() {
  local target="${1:-}"
  echo "Cleaning up scout worktrees..."

  local count=0
  for wt_dir in "$WORKTREE_BASE"/autodegen-*; do
    [[ -d "$wt_dir" ]] || continue
    [[ "$wt_dir" == "$REPO_DIR" ]] && continue  # don't clean main repo

    local name=$(basename "$wt_dir" | sed 's/autodegen-//')

    # If a specific target was given, skip non-matches
    if [[ -n "$target" && "$name" != "$target" ]]; then
      continue
    fi

    local branch="scout/${name}"

    git worktree remove "$wt_dir" --force 2>/dev/null || rm -rf "$wt_dir"
    git branch -D "$branch" 2>/dev/null || true

    echo "  ✓ removed $name"
    count=$((count + 1))
  done

  if [[ $count -eq 0 ]]; then
    echo "  No scout worktrees found."
  else
    echo "  Removed $count worktree(s)."
  fi
}

cmd_status() {
  echo "Active scout worktrees:"
  echo ""

  local found=0
  for wt_dir in "$WORKTREE_BASE"/autodegen-*; do
    [[ -d "$wt_dir" ]] || continue
    [[ "$wt_dir" == "$REPO_DIR" ]] && continue

    local name=$(basename "$wt_dir")
    local role=$(cat "$wt_dir/.role" 2>/dev/null || echo "unknown")
    local results=$(wc -l < "$wt_dir/results.tsv" 2>/dev/null || echo "0")
    local passes=$(grep -c "PASS" "$wt_dir/results.tsv" 2>/dev/null || echo "0")
    local best_composite=$(grep "PASS" "$wt_dir/results.tsv" 2>/dev/null | awk -F'\t' '{print $3}' | sort -rn | head -1)

    printf "  %-25s [%-14s] %3s iterations, %3s PASS" "$name" "$role" "$results" "$passes"
    if [[ -n "$best_composite" ]]; then
      printf ", best: %s" "$best_composite"
    fi
    echo ""
    found=1
  done

  if [[ $found -eq 0 ]]; then
    echo "  No active scout worktrees."
  fi
}

cmd_collect() {
  echo "Collecting PASS strategies from scouts..."

  local collected=0
  for wt_dir in "$WORKTREE_BASE"/autodegen-*; do
    [[ -d "$wt_dir" ]] || continue
    [[ "$wt_dir" == "$REPO_DIR" ]] && continue

    local name=$(basename "$wt_dir" | sed 's/autodegen-//')
    local role=$(cat "$wt_dir/.role" 2>/dev/null || echo "unknown")

    # Collect PASS results not already in leaderboard
    if [[ -f "$wt_dir/results.tsv" ]]; then
      local new=0
      while IFS= read -r line; do
        echo "$line" | grep -q "PASS" || continue
        # Append with source tag
        local tagged="${line}	${name}"
        # Check if already in leaderboard (by name + composite)
        local strat_name=$(echo "$line" | awk -F'\t' '{print $2}')
        local composite=$(echo "$line" | awk -F'\t' '{print $3}')
        if ! grep -qF "$strat_name	$composite" "$LEADERBOARD" 2>/dev/null; then
          echo "$tagged" >> "$LEADERBOARD"
          new=$((new + 1))
        fi
      done < "$wt_dir/results.tsv"
      if [[ $new -gt 0 ]]; then
        echo "  ✓ $name [$role]: collected $new new PASS result(s)"
        collected=$((collected + new))
      fi
    fi

    # Collect strategy files
    if [[ -f "$wt_dir/strategy.py" ]]; then
      local ts=$(date +%s)
      cp "$wt_dir/strategy.py" "$REPO_DIR/strategies/${name}_${ts}.py"
    fi

    # Collect stress reports
    if [[ "$role" == "stress-tester" && -f "$wt_dir/STRESS_REPORT.md" && -s "$wt_dir/STRESS_REPORT.md" ]]; then
      cp "$wt_dir/STRESS_REPORT.md" "$REPO_DIR/STRESS_REPORT_${name}.md"
      echo "  ✓ $name: collected stress report"
    fi
  done

  echo ""
  echo "Total new results collected: $collected"
}

# --- Main ---
case "${1:-}" in
  setup)
    shift
    setup_scout "$@"
    ;;
  setup-team)
    cmd_setup_team
    ;;
  cleanup)
    shift
    cmd_cleanup "${1:-}"
    ;;
  status)
    cmd_status
    ;;
  collect)
    cmd_collect
    ;;
  *)
    usage
    ;;
esac

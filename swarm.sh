#!/usr/bin/env bash
# autodegen swarm — git worktree lifecycle for parallel scout agents
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKTREE_BASE="$(dirname "$REPO_DIR")"
DATA_DIR="$REPO_DIR/data"
LEADERBOARD="$REPO_DIR/leaderboard.tsv"

usage() {
  cat <<EOF
Usage: swarm.sh <command> [args]

Commands:
  setup <n>       Create n scout worktrees (e.g. swarm.sh setup 5)
  cleanup         Remove all scout worktrees and branches
  status          Show active worktrees
  collect         Collect PASS strategies from all scouts into main repo

Example:
  ./swarm.sh setup 5    # create autodegen-scout-{1..5}
  # ... run agents ...
  ./swarm.sh collect    # harvest PASS results
  ./swarm.sh cleanup    # tear down worktrees
EOF
}

cmd_setup() {
  local n="${1:?Usage: swarm.sh setup <count>}"
  echo "Setting up $n scout worktrees..."

  for i in $(seq 1 "$n"); do
    local wt_dir="$WORKTREE_BASE/autodegen-scout-$i"
    local branch="scout/$i"

    # Clean up if leftover from previous run
    if git worktree list --porcelain | grep -q "$wt_dir"; then
      echo "  Cleaning leftover worktree: scout-$i"
      git worktree remove "$wt_dir" --force 2>/dev/null || true
      git branch -D "$branch" 2>/dev/null || true
    fi

    # Create worktree on a throwaway branch from HEAD
    git worktree add "$wt_dir" -b "$branch" HEAD 2>/dev/null
    
    # Symlink shared data directory (read-only, all scouts share same data)
    rm -rf "$wt_dir/data"
    ln -sfn "$DATA_DIR" "$wt_dir/data"
    
    # Symlink leaderboard (shared across all scouts)
    rm -f "$wt_dir/leaderboard.tsv"
    ln -sfn "$LEADERBOARD" "$wt_dir/leaderboard.tsv"
    
    # Fresh results.tsv for this scout (don't inherit main's history)
    : > "$wt_dir/results.tsv"

    echo "  ✓ scout-$i ready at $wt_dir"
  done

  echo ""
  echo "All $n scouts ready. Data symlinked, leaderboard shared."
  echo "Scouts can git commit/revert independently."
}

cmd_cleanup() {
  echo "Cleaning up scout worktrees..."
  
  local count=0
  for wt_dir in "$WORKTREE_BASE"/autodegen-scout-*; do
    [ -d "$wt_dir" ] || continue
    local i=$(basename "$wt_dir" | sed 's/autodegen-scout-//')
    local branch="scout/$i"
    
    git worktree remove "$wt_dir" --force 2>/dev/null || rm -rf "$wt_dir"
    git branch -D "$branch" 2>/dev/null || true
    
    echo "  ✓ removed scout-$i"
    count=$((count + 1))
  done

  # Prune stale worktree refs
  git worktree prune
  
  echo "Cleaned up $count scout worktrees."
}

cmd_status() {
  echo "Active worktrees:"
  git worktree list
  echo ""
  echo "Scout directories:"
  ls -d "$WORKTREE_BASE"/autodegen-scout-* 2>/dev/null || echo "  (none)"
}

cmd_collect() {
  echo "Collecting PASS strategies from scouts..."
  
  local collected=0
  for wt_dir in "$WORKTREE_BASE"/autodegen-scout-*; do
    [ -d "$wt_dir" ] || continue
    local scout_name=$(basename "$wt_dir")
    
    # Check for PASS results in this scout's results.tsv
    if [ -f "$wt_dir/results.tsv" ] && grep -q "PASS" "$wt_dir/results.tsv"; then
      local pass_count=$(grep -c "PASS" "$wt_dir/results.tsv")
      echo "  $scout_name: $pass_count PASS result(s)"
      
      # Copy the current strategy.py if it produced a PASS
      local ts=$(date +%s)
      cp "$wt_dir/strategy.py" "$REPO_DIR/strategies/${scout_name}_${ts}.py" 2>/dev/null || {
        mkdir -p "$REPO_DIR/strategies"
        cp "$wt_dir/strategy.py" "$REPO_DIR/strategies/${scout_name}_${ts}.py"
      }
      
      # Append PASS rows to main results.tsv (with source tag)
      grep "PASS" "$wt_dir/results.tsv" | while IFS= read -r line; do
        echo -e "${line}\t${scout_name}" >> "$LEADERBOARD"
      done
      
      collected=$((collected + pass_count))
    else
      echo "  $scout_name: no PASS results"
    fi
  done
  
  echo ""
  echo "Collected $collected PASS result(s) into leaderboard.tsv + strategies/"
}

# Dispatch
cd "$REPO_DIR"
case "${1:-}" in
  setup)   cmd_setup "${2:-}" ;;
  cleanup) cmd_cleanup ;;
  status)  cmd_status ;;
  collect) cmd_collect ;;
  *)       usage ;;
esac

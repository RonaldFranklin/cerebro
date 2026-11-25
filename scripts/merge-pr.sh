#!/usr/bin/env bash
# Merge a pull request only when every check is green. The only sanctioned way to merge (AGENTS.md section 5).
# Usage: scripts/merge-pr.sh <pr-number>
set -euo pipefail

pr="${1:?usage: scripts/merge-pr.sh <pr-number>}"
[[ "$pr" =~ ^[0-9]+$ ]] || { echo "error: PR number must be an integer" >&2; exit 2; }

branch="$(gh pr view "$pr" --json headRefName -q .headRefName)"

# GitHub can lag behind the branch tip; never merge a head that is not the pushed tip.
for _ in $(seq 1 30); do
  pr_head="$(gh pr view "$pr" --json headRefOid -q .headRefOid)"
  remote_head="$(git ls-remote origin "refs/heads/$branch" | cut -f1)"
  [[ -n "$remote_head" && "$pr_head" == "$remote_head" ]] && break
  sleep 10
done
[[ "$pr_head" == "$remote_head" ]] || { echo "error: PR head $pr_head differs from branch tip $remote_head" >&2; exit 1; }

# Wait for CI; fail on the first failing check, and refuse when no check exists at all.
checks="$(gh pr checks "$pr" 2>&1 || true)"
if grep -qi "no checks reported" <<<"$checks"; then
  for _ in $(seq 1 12); do
    sleep 10
    checks="$(gh pr checks "$pr" 2>&1 || true)"
    grep -qi "no checks reported" <<<"$checks" || break
  done
  grep -qi "no checks reported" <<<"$checks" && { echo "error: no checks reported for PR $pr" >&2; exit 1; }
fi
gh pr checks "$pr" --watch --fail-fast
gh pr checks "$pr" | awk -F'\t' '$2 != "pass" && $2 != "skipping" { bad=1 } END { exit bad }' \
  || { echo "error: not every check passed" >&2; exit 1; }

# Merge exactly the head that was verified; retry transient GitHub errors.
for attempt in 1 2 3 4; do
  if gh pr merge "$pr" --squash --delete-branch --match-head-commit "$pr_head"; then
    echo "merged PR $pr at $pr_head"
    exit 0
  fi
  [[ "$(gh pr view "$pr" --json state -q .state)" == "MERGED" ]] && { echo "merged PR $pr"; exit 0; }
  sleep $((attempt * 10))
done
echo "error: could not merge PR $pr" >&2
exit 1

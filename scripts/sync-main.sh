#!/usr/bin/env bash
# Take in whatever a push left on the branch, regenerate the feeds and push
# the result. Run from the root of a checkout that can push.
#
#   scripts/sync-main.sh [remote] [branch]
#
# The outcome of the last attempt is left in $SYNC_RESULT (sync-result.json).
# A push that loses a race is retried against the new tip, so a publisher
# commit that lands while this runs is taken in rather than overwritten.
set -euo pipefail

remote="${1:-origin}"
branch="${2:-main}"
result="${SYNC_RESULT:-sync-result.json}"

for attempt in 1 2 3 4; do
  git fetch --quiet "$remote" "$branch"
  git reset --quiet --hard "$remote/$branch"
  python3 scripts/publish.py sync > "$result"
  git add -A -- feed.json latest.json posts archive/feed.json
  if git diff --cached --quiet; then
    exit 0
  fi
  git commit --quiet -m "$(python3 scripts/publish.py sync-subject "$result")"
  if git push --quiet "$remote" "HEAD:$branch"; then
    exit 0
  fi
  sleep $((attempt * 2))
done

echo "could not push the regenerated feeds to $branch" >&2
exit 1

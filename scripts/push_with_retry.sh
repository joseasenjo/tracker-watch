#!/usr/bin/env bash
# Rebase on whatever others pushed in the meantime and push, trying again when another push wins the race. The jobs that
# commit their results (reports, drafts, trends) used to fail on a single lost race and the whole run's data was lost
# (2026-10-20: the blocking-list test measured everything and then could not push).
set -u
for attempt in 1 2 3 4 5 6; do
  if git pull --rebase --autostash && git push; then
    exit 0
  fi
  echo "push attempt $attempt failed; trying again" >&2
  sleep $((attempt * 5))
done
echo "could not push after 6 attempts" >&2
exit 1

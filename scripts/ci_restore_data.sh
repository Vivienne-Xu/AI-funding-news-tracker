#!/usr/bin/env bash
# Scheduled runs only. Loads the saved database (and past monthly reports) from the `data` branch into this run.
# If this fails, the run stops before touching anything, so saved data can never be overwritten by an empty database.
set -euo pipefail

REMOTE="${DATA_REMOTE:-https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git}"  # DATA_REMOTE: for local testing only
rm -rf data-branch
mkdir -p data reports

if git ls-remote --exit-code --heads "$REMOTE" data >/dev/null 2>&1; then
  git clone --quiet --depth 1 --branch data "$REMOTE" data-branch
  for file in data-branch/*.db; do
    [ -e "$file" ] && cp -f "$file" data/
  done
  if [ -d data-branch/reports ]; then
    cp -R data-branch/reports/. reports/
  fi
  echo "Restored the database from the data branch."
else
  echo "No data branch yet (first run): starting with an empty database."
fi

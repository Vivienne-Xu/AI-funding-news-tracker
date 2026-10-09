#!/usr/bin/env bash
# Scheduled runs only. Saves the database (and monthly reports) to the `data` branch.
# The branch always holds a single commit, so the repository does not grow every day.
# The workflow's "data-branch" lock makes sure two runs never save at the same time.
set -euo pipefail

if [ ! -f data/funding.db ]; then
  echo "No database was produced, so nothing is saved."
  exit 0
fi

REMOTE="${DATA_REMOTE:-https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git}"  # DATA_REMOTE: for local testing only
rm -rf data-branch
mkdir data-branch
cp data/funding.db data-branch/
[ -f data/ai_cache.db ] && cp data/ai_cache.db data-branch/
[ -d reports ] && cp -R reports data-branch/reports

cd data-branch
echo "* -text" > .gitattributes   # never change line endings inside the database file
git init --quiet --initial-branch=data
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add -A
git commit --quiet -m "Data update $(date -u +%F)"
git push --quiet --force "$REMOTE" data
echo "Saved the database to the data branch."

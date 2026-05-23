#!/usr/bin/env bash
# github_status.sh - sync state of every GitHub/holbizmetrics repo under a root.
# Usage: bash github_status.sh [root]      default root: /c/FromGithubEtc
# Per repo: branch ...origin [ahead/behind] + uncommitted + OK/WARN + open-PR count.
# The open-PR count needs the GitHub CLI gh installed and authed; without it, skipped.
# EXCLUDE = space-separated folder names to skip (forks / not-yours, e.g. maui-samples
# whose .git remote is scrambled to a holbizmetrics repo but whose content is upstream).
set -u
root="${1:-/c/FromGithubEtc}"
EXCLUDE=" maui-samples "
cd "$root" || { echo "cannot cd to: $root" >&2; exit 1; }
have_gh=0; command -v gh >/dev/null 2>&1 && have_gh=1

for d in */; do
  d="${d%/}"
  [ -d "$d/.git" ] || continue
  case "$EXCLUDE" in *" $d "*) continue ;; esac

  url=$(GIT_TERMINAL_PROMPT=0 git -C "$d" remote get-url origin 2>/dev/null)
  case "$url" in
    *github.com[:/]holbizmetrics/*) : ;;
    *) continue ;;
  esac

  GIT_TERMINAL_PROMPT=0 git -C "$d" fetch -q 2>/dev/null
  sb=$(git -C "$d" status -sb 2>/dev/null | head -1 | sed 's|## ||')
  unc=$(git -C "$d" status --porcelain 2>/dev/null | wc -l | tr -d ' ')

  if [ "$unc" != "0" ]; then
    st="WARN: $unc uncommitted"
  elif printf '%s' "$sb" | grep -q '\['; then
    st="WARN: ahead/behind"
  elif ! printf '%s' "$sb" | grep -q '[.][.][.]'; then
    st="WARN: no upstream"
  else
    st="OK: current"
  fi

  pr=""
  if [ "$have_gh" = "1" ]; then
    slug=$(printf '%s' "$url" | sed -E 's#.*github.com[:/]##; s#\.git$##')
    n=$(gh pr list -R "$slug" --state open --json number -q 'length' 2>/dev/null)
    case "${n:-}" in
      ''|0) : ;;
      *) pr="  | $n open PR(s)" ;;
    esac
  fi

  printf '%-30s %-46s %s%s\n' "$d" "$sb" "$st" "$pr"
done

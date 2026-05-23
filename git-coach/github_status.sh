#!/usr/bin/env bash
# github_status.sh - sync state of every GitHub/holbizmetrics repo under a root.
# Usage: bash github_status.sh [root]      default root: /c/FromGithubEtc
# Per repo prints: branch ...origin [ahead/behind] + uncommitted count + OK/WARN.
set -u
root="${1:-/c/FromGithubEtc}"
cd "$root" || { echo "cannot cd to: $root" >&2; exit 1; }

for d in */; do
  d="${d%/}"
  [ -d "$d/.git" ] || continue

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

  printf '%-30s %-46s %s\n' "$d" "$sb" "$st"
done

#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "usage: $0 <project-name>" >&2
  exit 1
fi

name="$1"
root="$HOME/AI_Agent/projects/$name"
wiki="$root/wiki"

if [ -e "$root" ]; then
  echo "project already exists: $root" >&2
  exit 1
fi

mkdir -p "$wiki"

for f in roadmap.md decisions.md architecture.md tasks.md lessons-learned.md runbook.md scratchpad.md; do
  touch "$wiki/$f"
  printf "# %s\n" "$(basename "$f" .md)" > "$wiki/$f"
done

touch "$root/run-log.jsonl"

echo "created project: $root"

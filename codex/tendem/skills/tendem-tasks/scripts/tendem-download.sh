#!/usr/bin/env bash
#
# Download Tendem result files (pre-signed URLs from get_task_result).
#
# Usage:
#   tendem-download.sh <dest_dir> <name> '<download_url>' [<name> '<download_url>' ...]
#
#   <dest_dir>  Where files land, e.g. ./tendem/<task_id> under the project.
#   <name>      The files[].name from get_task_result. May be a full path
#               (e.g. /mnt/data/report.pdf) — only its basename is used.
#   <url>       The matching files[].download_url. QUOTE IT — it contains '&'.
#               Valid ~24h; if expired, re-call get_task_result for fresh URLs.

set -euo pipefail

if [ $# -lt 3 ] || [ $(( ($# - 1) % 2 )) -ne 0 ]; then
  echo "usage: $0 <dest_dir> <name> '<url>' [<name> '<url>' ...]" >&2
  exit 2
fi

DEST="$1"; shift
mkdir -p "$DEST"

i=0
while [ $# -gt 0 ]; do
  name="$1"; url="$2"; shift 2
  safe="$(basename "$name")"
  [ -n "$safe" ] && [ "$safe" != "/" ] || safe="file-$i"
  if curl -fSL --max-time 300 -o "$DEST/$safe" "$url" 2>/dev/null; then
    echo "saved: $DEST/$safe"
  else
    echo "error: download of $safe failed (URL expired? re-call get_task_result)" >&2
    exit 1
  fi
  i=$((i + 1))
done

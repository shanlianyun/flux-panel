#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT
CGO_ENABLED=0 go build -o "$work_dir/gost" .
cd "$work_dir"

check_version() {
  local output
  output="$(./gost -V)"
  [[ "$output" == gost\ * ]]
}

# Version probes must not load configuration, including broken existing files.
check_version
[[ ! -e config.json && ! -e gost.json ]]
printf '%s\n' 'invalid-json' > config.json
check_version
[[ "$(cat config.json)" == 'invalid-json' ]]

# Real startup must still reject missing or invalid configuration.
if ./gost > startup.log 2>&1; then
  echo 'startup unexpectedly accepted invalid config' >&2
  exit 1
fi
grep -q 'invalid character' startup.log || { cat startup.log >&2; exit 1; }
rm config.json
if ./gost > startup.log 2>&1; then
  echo 'startup unexpectedly accepted missing config' >&2
  exit 1
fi
grep -q 'config.json' startup.log || { cat startup.log >&2; exit 1; }
echo 'gost CLI checks passed'

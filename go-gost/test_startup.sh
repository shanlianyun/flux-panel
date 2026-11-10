#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

# Main parses flags in init.
# Run the compiled tests without testing flags and from an isolated fixture directory.
CGO_ENABLED=0 go test -c -o "$work_dir/startup.test" .
printf '%s\n' '{"addr":"127.0.0.1:1","secret":"startup-fixture"}' > "$work_dir/config.json"
cd "$work_dir"
./startup.test

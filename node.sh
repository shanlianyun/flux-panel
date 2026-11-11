#!/usr/bin/env bash
# Define only. The launcher appends the call after curl succeeds, so even a
# complete response followed by a transfer error cannot start installation.
main() (
  set -euo pipefail
  umask 077

  if [[ $# -ne 2 || -z "$1" || -z "$2" ]]; then
    echo 'Usage: node.sh <panel-host:port> <node-secret>' >&2
    exit 2
  fi

  bootstrap_dir=$(mktemp -d "${TMPDIR:-/tmp}/flux-node.XXXXXX")
  trap 'rm -rf -- "$bootstrap_dir"' EXIT
  installer="$bootstrap_dir/install.sh"
  curl --fail --silent --show-error --location --retry 3 \
    --connect-timeout 15 --max-time 120 \
    https://raw.githubusercontent.com/shanlianyun/flux-panel/main/install.sh \
    -o "$installer"
  if [[ ! -s "$installer" ]]; then
    echo 'Downloaded installer is empty; installation cancelled.' >&2
    exit 1
  fi
  bash -n "$installer"
  bash "$installer" install -a "$1" -s "$2"
)

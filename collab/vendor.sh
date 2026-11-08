#!/usr/bin/env bash
# Fetch omp's own collab guest code (MIT, github.com/can1357/oh-my-pi) at one omp
# version into vendor/. The helper speaks the protocol through these files only,
# so following an omp update is: ./vendor.sh <new version>, then run the checks.
#   ./vendor.sh 18.15.0
set -euo pipefail
ver=${1:?usage: vendor.sh <omp version, e.g. 18.15.0>}
cd "$(dirname "$0")"
base="https://raw.githubusercontent.com/can1357/oh-my-pi/v$ver/packages"
rm -rf vendor.new
mkdir -p vendor.new/pi-wire vendor.new/web
for f in index stream tsp; do curl -fsS -o "vendor.new/pi-wire/$f.ts" "$base/wire/src/$f.ts"; done
for f in client codec link socket jsonl; do curl -fsS -o "vendor.new/web/$f.ts" "$base/collab-web/src/lib/$f.ts"; done
curl -fsS -o vendor.new/LICENSE "https://raw.githubusercontent.com/can1357/oh-my-pi/v$ver/LICENSE"
echo "$ver" > vendor.new/VERSION
rm -rf vendor && mv vendor.new vendor
echo "vendored omp $ver collab guest code"

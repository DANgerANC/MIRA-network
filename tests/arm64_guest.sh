#!/usr/bin/env bash
# Executed as the ordinary ubuntu user inside the emulated ARM64 OS.
set -euo pipefail
BASE=http://10.0.2.2:18787
WORK=$(mktemp -d "$HOME/mira-arm64-XXXXXXXX")
cd "$WORK"
exec > >(tee arm64-test.log) 2>&1
finish() {
  rc=$?
  printf 'exit=%s\nwork=%s\n' "$rc" "$WORK" > arm64-guest-progress.txt
  for file in arm64-env.txt arm64.json arm64-test.log arm64-guest-progress.txt; do
    if [ -f "$file" ]; then curl -fsS --max-time 15 -T "$file" "$BASE/$file" || true; fi
  done
}
trap finish EXIT
{
  cat /etc/os-release
  uname -a
  id
  getconf GNU_LIBC_VERSION
  python3 --version
  lscpu
  df -h
  ip -brief addr
} > arm64-env.txt
test "$(uname -m)" = aarch64
test "$(id -u)" -ne 0
curl -fsS "$BASE/mira" -o mira
curl -fsS "$BASE/cross_platform.py" -o cross_platform.py
chmod +x mira
ldd ./mira >> arm64-env.txt
sha256sum mira >> arm64-env.txt
curl -fsS -T arm64-env.txt "$BASE/arm64-env.txt"
python3 cross_platform.py ./mira --report arm64.json

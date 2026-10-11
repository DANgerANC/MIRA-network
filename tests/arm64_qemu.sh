#!/usr/bin/env bash
# Cross-build on Linux x86-64; run the full acceptance scenario in ARM64 Linux.
# Dependencies: qemu-system-arm qemu-utils qemu-efi-aarch64 cloud-image-utils
#               gcc-aarch64-linux-gnu libc6-dev-arm64-cross,
#               Rust/rustup, curl, Python 3.9+.
# Optional MIRA_REPORT_UPLOAD_URL copies evidence to a local supervising host.
set -euo pipefail
SOURCE=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$SOURCE/target"
WORK=$(mktemp -d "$SOURCE/target/arm64-qemu-XXXXXXXX")
UPLOAD=${MIRA_REPORT_UPLOAD_URL:-}
QEMU_PID=
SERVER_PID=
exec > >(tee "$WORK/arm64-build.log") 2>&1

upload() {
  if [ -n "$UPLOAD" ] && [ -s "$WORK/$1" ]; then
    curl -fsS --max-time 10 -T "$WORK/$1" "$UPLOAD/$1" || true
  fi
}
stage() {
  printf 'stage=%s\nwork=%s\ntime=%s\n' "$1" "$WORK" "$(date -u +%FT%TZ)" > "$WORK/arm64-progress.txt"
  cat "$WORK/arm64-progress.txt"
  upload arm64-progress.txt
}
finish() {
  rc=$?
  trap - EXIT
  for process in "$QEMU_PID" "$SERVER_PID"; do
    if [ -n "$process" ] && kill -0 "$process" 2>/dev/null; then
      kill "$process" || true
      wait "$process" || true
    fi
  done
  printf 'stage=finished\nexit=%s\nwork=%s\n' "$rc" "$WORK" > "$WORK/arm64-progress.txt"
  for file in arm64-build.log arm64-console.log arm64-env.txt arm64.json arm64-test.log \
              arm64-guest-progress.txt arm64-build-env.txt arm64-progress.txt; do
    upload "$file"
  done
  exit "$rc"
}
trap finish EXIT

stage prerequisites
for tool in qemu-system-aarch64 qemu-img cloud-localds aarch64-linux-gnu-gcc rustup cargo curl python3; do
  command -v "$tool"
done
FIRMWARE=/usr/share/qemu-efi-aarch64/QEMU_EFI.fd
test -f "$FIRMWARE"
test -f "$(aarch64-linux-gnu-gcc -print-file-name=Scrt1.o)"
{
  uname -a
  rustc -Vv
  cargo -V
  qemu-system-aarch64 --version
  aarch64-linux-gnu-gcc --version
} > "$WORK/arm64-build-env.txt"

stage cross-build
cd "$SOURCE"
rustup target add aarch64-unknown-linux-gnu
export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=aarch64-linux-gnu-gcc
cargo build --release --locked --target aarch64-unknown-linux-gnu
mkdir "$WORK/payload"
cp target/aarch64-unknown-linux-gnu/release/mira "$WORK/payload/mira"
cp tests/cross_platform.py "$WORK/payload/cross_platform.py"
cp tests/arm64_guest.sh "$WORK/payload/guest.sh"
{
  aarch64-linux-gnu-readelf -h "$WORK/payload/mira"
  sha256sum "$WORK/payload/mira"
} >> "$WORK/arm64-build-env.txt"
upload arm64-build-env.txt

stage download-image
cd "$WORK"
IMAGE=noble-server-cloudimg-arm64.img
IMAGE_URL=https://cloud-images.ubuntu.com/noble/20260926
IMAGE_CACHE="$SOURCE/target/qemu-images/20260926"
mkdir -p "$IMAGE_CACHE"
if [ ! -f "$IMAGE_CACHE/$IMAGE" ]; then
  curl -fL --connect-timeout 30 --max-time 1200 --retry 3 "$IMAGE_URL/$IMAGE" -o "$IMAGE_CACHE/$IMAGE.part"
  mv "$IMAGE_CACHE/$IMAGE.part" "$IMAGE_CACHE/$IMAGE"
fi
curl -fsS --max-time 60 "$IMAGE_URL/SHA256SUMS" -o SHA256SUMS
awk -v image="$IMAGE" '$2 == "*" image || $2 == image { print }' SHA256SUMS > image.sha256
test -s image.sha256
(cd "$IMAGE_CACHE" && sha256sum -c "$WORK/image.sha256")
cat image.sha256 >> arm64-build-env.txt
printf 'image_url=%s/%s\n' "$IMAGE_URL" "$IMAGE" >> arm64-build-env.txt
qemu-img create -f qcow2 -F qcow2 -b "$IMAGE_CACHE/$IMAGE" guest.qcow2
qemu-img resize guest.qcow2 8G

python3 - <<'PY'
import json
from pathlib import Path
config = {
    "hostname": "mira-arm64-qemu",
    "users": ["default"],
    "ssh_pwauth": False,
    "runcmd": [
        ["bash", "-c", "curl -fsS --retry 5 http://10.0.2.2:18787/guest.sh -o /tmp/mira-arm64-guest.sh && runuser -u ubuntu -- bash /tmp/mira-arm64-guest.sh"],
        ["poweroff"],
    ],
}
Path("user-data").write_text("#cloud-config\n" + json.dumps(config), encoding="utf-8")
Path("meta-data").write_text("instance-id: mira-arm64-qemu\nlocal-hostname: mira-arm64-qemu\n", encoding="utf-8")
PY
cloud-localds seed.img user-data meta-data
python3 "$SOURCE/tests/qemu_result_server.py" "$WORK" > result-server.log 2>&1 &
SERVER_PID=$!
stage boot-arm64
QEMU_ARGS=(-machine virt -accel tcg,thread=multi -cpu cortex-a72 -smp 2 -m 1536
           -bios "$FIRMWARE" -display none -monitor none -serial "file:$WORK/arm64-console.log"
           -drive "if=none,file=$WORK/guest.qcow2,format=qcow2,id=root"
           -device virtio-blk-pci,drive=root
           -drive "if=none,file=$WORK/seed.img,format=raw,id=seed"
           -device virtio-blk-pci,drive=seed
           -netdev user,id=net -device virtio-net-pci,netdev=net,romfile= -no-reboot)
printf 'qemu-system-aarch64 ' >> arm64-build-env.txt
printf '%q ' "${QEMU_ARGS[@]}" >> arm64-build-env.txt
printf '\n' >> arm64-build-env.txt
upload arm64-build-env.txt
qemu-system-aarch64 "${QEMU_ARGS[@]}" > qemu.stderr.log 2>&1 &
QEMU_PID=$!
deadline=$((SECONDS + 1200))
while [ ! -f arm64-guest-progress.txt ]; do
  if ! kill -0 "$QEMU_PID" 2>/dev/null; then
    cat qemu.stderr.log
    echo 'QEMU exited before the guest returned an acceptance result' >&2
    exit 1
  fi
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo 'ARM64 guest timed out' >&2
    exit 1
  fi
  sleep 10
  upload arm64-console.log
  upload arm64-env.txt
  upload arm64.json
done
stage verify-result
cat arm64-guest-progress.txt
python3 - <<'PY'
import json
from pathlib import Path
report = json.loads(Path("arm64.json").read_text(encoding="utf-8"))
assert report["machine"] == "aarch64", report["machine"]
assert report["system"]["architecture"] == "aarch64", report["system"]
assert report["passed"] and all(c["passed"] for c in report["checks"]), report.get("error")
print("ARM64 QEMU: {} checks passed".format(len(report["checks"])))
PY
cat arm64-test.log
# Give the disposable guest time to shut down normally after cloud-init's test.
for _ in $(seq 1 30); do
  if ! kill -0 "$QEMU_PID" 2>/dev/null; then break; fi
  sleep 1
done
echo "Evidence: $WORK"

#!/bin/sh
# Run the separately identified, expanded intrinsic backends on all four CPUs.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
case "${1:-}" in -h|--help) echo "Usage: $0 [output-directory] [extended-generate options, e.g. --name _mm_abs_epi8]"; exit 0 ;; esac
output=${1:-"$root/artifacts/extended"}
if [ "$#" -gt 0 ]; then shift; fi
mkdir -p "$output"
output=$(CDPATH= cd -- "$output" && pwd)
if [ -e "$output/vectors/inputs.manifest.json" ]; then echo 'Choose a fresh output directory.' >&2; exit 2; fi
prefix=${EQUILANE_IMAGE_PREFIX:-equilane/ubuntu26.04}
catalog=${EQUILANE_EXTENDED_CATALOG:-/workspace/contracts/simde-pure-values.json}
count=${EQUILANE_EXTENDED_COUNT:-32}
case "$count" in ''|*[!0-9]*|0) echo 'EQUILANE_EXTENDED_COUNT must be positive.' >&2; exit 2 ;; esac
host=$(docker info --format '{{.Architecture}}')
case "$host" in aarch64|arm64) host=arm64; comparator=arm ;; x86_64|amd64) host=amd64; comparator=intel ;; ppc64le|powerpc64le) host=ppc64le; comparator=openpower ;; riscv64) comparator=riscv ;; *) echo 'Unsupported Docker host architecture' >&2; exit 2 ;; esac
mkdir -p "$output/images"
configure() {
  role=$1; cpu=
  case "$role" in intel) platform=amd64 ;; arm) platform=arm64 ;; openpower) platform=ppc64le; cpu=power9 ;; riscv) platform=riscv64; cpu=${EQUILANE_RISCV_CPU:-rva23s64,vlen=128} ;; esac
  if [ "$platform" = "$host" ]; then cpu=; fi
}
for role in intel openpower arm riscv; do
  configure "$role"
  if ! docker image inspect "$prefix:$role" --format '{{.Id}}' > "$output/images/$role.id" 2>/dev/null; then
    echo "Building missing $role environment" >&2
    docker build --platform "linux/$platform" --build-arg "QEMU_CPU=$cpu" --tag "$prefix:$role" "$root"
    docker image inspect "$prefix:$role" --format '{{.Id}}' > "$output/images/$role.id"
  fi
done
run() {
  configure "$1"; shift
  image=$(cat "$output/images/$role.id")
  docker run --rm --platform "linux/$platform" \
    --mount "type=bind,src=$root,dst=/workspace,readonly" \
    --mount "type=bind,src=$output,dst=/results" -w /workspace \
    -e PYTHONPATH=/workspace/src -e PYTHONDONTWRITEBYTECODE=1 \
    -e "EQUILANE_IMAGE_ID=$image" -e "QEMU_CPU=$cpu" "$image" "$@"
}
run "$comparator" python3 -m equilane extended-generate --catalog "$catalog" --output /results/vectors --count-per-case "$count" "$@"
status=0
for role in intel openpower arm riscv; do
  code=0
  run "$role" python3 -m equilane extended-run --input /results/vectors --output "/results/native/$role" --role "$role" || code=$?
  if [ "$code" -gt "$status" ]; then status=$code; fi
done
for role in openpower arm riscv; do
  code=0
  run "$comparator" python3 -m equilane extended-compare --intel /results/native/intel --target "/results/native/$role" --output "/results/compare-$role" || code=$?
  if [ "$code" -gt "$status" ]; then status=$code; fi
done
run "$comparator" python3 /workspace/docker/extended_index.py --input /results
exit "$status"

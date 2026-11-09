#!/bin/sh
# Host requirements: a POSIX shell and Docker; no host Python or C compiler.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
action=${1:-all}
case "$action" in
    all|verify|wrappers|intel|openpower|arm|riscv|compare|report) ;;
    -h|--help)
        echo "Usage: $0 [all|verify|wrappers|intel|openpower|arm|riscv|compare|report] [output-directory]"
        exit 0 ;;
    *) echo "Unknown action: $action" >&2; exit 2 ;;
esac
if [ "$#" -gt 2 ]; then echo "At most two arguments are accepted" >&2; exit 2; fi
output=${2:-"$root/artifacts/docker"}
mkdir -p "$output"
output=$(CDPATH= cd -- "$output" && pwd)
prefix=${EQUILANE_IMAGE_PREFIX:-equilane/ubuntu26.04}
jobs=${EQUILANE_JOBS:-2}
case "$jobs" in ''|*[!0-9]*|0) echo "EQUILANE_JOBS must be a positive integer" >&2; exit 2 ;; esac
mkdir -p "$output/launcher-errors" "$output/images"
status=0

record_error() {
    # Identifier and phase are fixed internal words, never raw shell output.
    printf '{"status":"error","stage":"%s","exit_code":%s}\n' "$2" "$3" > "$output/launcher-errors/$1.json"
    status=2
}

merge_status() {
    if [ "$1" -gt 1 ]; then status=2
    elif [ "$1" -eq 1 ] && [ "$status" -eq 0 ]; then status=1
    fi
}

invalidate() {
    rm -f "$output/pipeline-summary.json" "$output/pipeline-report.html" "$output/compare-openpower.json" "$output/compare-arm.json" "$output/compare-riscv.json"
    case "$action" in
        all|verify|wrappers)
            for target in intel openpower arm riscv riscv-vlen256; do
                mkdir -p "$output/$target"
                rm -f "$output/$target/manifest.json"
            done
            rm -f "$output/launcher-errors/"*.json ;;
        intel|openpower|arm|riscv)
            mkdir -p "$output/$action"
            rm -f "$output/$action/manifest.json" "$output/launcher-errors/$action-"*.json ;;
        compare|report) rm -f "$output/launcher-errors/compare-"*.json "$output/launcher-errors/report.json" ;;
    esac
    if [ "$action" = verify ]; then
        rm -f "$output/quality/manifest.json"
        for target in intel openpower arm riscv; do rm -f "$output/native-tests/$target/manifest.json"; done
    fi
    case "$action" in
        all|verify)
            rm -f "$output/vectors/test-vectors.manifest.json"
            for target in intel openpower arm riscv; do
                case "$target" in openpower) stem=power ;; *) stem=$target ;; esac
                rm -f "$output/native/$target/$stem-results.manifest.json" "$output/native/$target/native-traceability.json"
            done
            rm -f "$output/native/riscv-vlen256/riscv-results.manifest.json" "$output/native/riscv-vlen256/native-traceability.json"
            canonical_archive=
            for target in openpower arm riscv riscv-vlen256; do
                if [ -e "$output/canonical/compare-$target" ]; then
                    if [ -z "$canonical_archive" ]; then
                        mkdir -p "$output/canonical/history"
                        canonical_archive=$(mktemp -d "$output/canonical/history/run.XXXXXX")
                    fi
                    mv "$output/canonical/compare-$target" "$canonical_archive/compare-$target"
                fi
            done ;;
    esac
}

fallback_report() {
    printf '%s\n' '{"artifact_type":"equilane.docker-pipeline","schema_version":1,"status":"error","exit_code":2,"error":"Docker could not generate the complete report; inspect launcher-errors and command logs"}' > "$output/pipeline-summary.json"
    printf '%s\n' '<!doctype html><html lang="en"><meta charset="utf-8"><title>EquiLane Docker verification</title><h1>Docker verification failed</h1><p>Docker could not generate the complete report. Inspect the launcher-errors directory and command logs.</p><a href="pipeline-summary.json">Run status</a></html>' > "$output/pipeline-report.html"
}

invalidate
host_arch=$(docker info --format '{{.Architecture}}') || {
    code=$?; record_error docker connection "$code"; fallback_report; exit 2;
}
case "$host_arch" in
    aarch64|arm64) host_arch=arm64; host_role=arm ;;
    x86_64|amd64) host_arch=amd64; host_role=intel ;;
    powerpc64le|ppc64le) host_arch=ppc64le; host_role=openpower ;;
    riscv64) host_role=riscv ;;
    *) host_role=intel ;;
esac
git_commit=${EQUILANE_GIT_COMMIT:-unknown}
if [ "$git_commit" = unknown ] && command -v git >/dev/null 2>&1; then
    git_commit=$(git -C "$root" rev-parse --verify HEAD 2>/dev/null) || git_commit=unknown
fi

configure_role() {
    active_role=$1
    active_cpu=
    case "$active_role" in
        intel) active_arch=amd64 ;;
        openpower) active_arch=ppc64le; active_cpu=power9 ;;
        arm) active_arch=arm64 ;;
        riscv) active_arch=riscv64; active_cpu=${EQUILANE_RISCV_CPU:-rva23s64,vlen=128} ;;
    esac
    active_mode=docker-native
    if [ "$active_arch" != "$host_arch" ]; then active_mode=docker-emulated; else active_cpu=; fi
}

build_image() {
    configure_role "$1"
    iidfile="$output/images/$active_role.id"
    rm -f "$iidfile"
    if [ "${EQUILANE_BUILD:-1}" != 0 ]; then
        echo "Building $active_role (linux/$active_arch)" >&2
        if [ -n "$active_cpu" ]; then
            docker build --platform "linux/$active_arch" --build-arg "QEMU_CPU=$active_cpu" \
                --build-arg "EQUILANE_GIT_COMMIT=$git_commit" --iidfile "$iidfile" --tag "$prefix:$active_role" "$root" || return $?
        else
            docker build --platform "linux/$active_arch" --build-arg "EQUILANE_GIT_COMMIT=$git_commit" \
                --iidfile "$iidfile" --tag "$prefix:$active_role" "$root" || return $?
        fi
    else
        docker image inspect --format '{{.Id}}' "$prefix:$active_role" > "$iidfile" || return $?
    fi
    image_id=$(cat "$iidfile")
    case "$image_id" in sha256:*) ;; *) echo "Missing Docker image identity" >&2; return 2 ;; esac
}

run_target() {
    configure_role "$1"
    shift
    image_id=$(cat "$output/images/$active_role.id") || return $?
    case "$image_id" in sha256:*) ;; *) return 2 ;; esac
    if [ -n "$active_cpu" ]; then
        docker run --rm --platform "linux/$active_arch" --mount "type=bind,src=$output,dst=/results" \
            -e "EQUILANE_EXECUTION_MODE=$active_mode" -e "EQUILANE_IMAGE_ID=$image_id" \
            -e "EQUILANE_HOST_ARCH=$host_arch" -e "EQUILANE_DOCKER_PLATFORM=linux/$active_arch" \
            -e "QEMU_CPU=$active_cpu" "$image_id" "$@"
    else
        docker run --rm --platform "linux/$active_arch" --mount "type=bind,src=$output,dst=/results" \
            -e "EQUILANE_EXECUTION_MODE=$active_mode" -e "EQUILANE_IMAGE_ID=$image_id" \
            -e "EQUILANE_HOST_ARCH=$host_arch" -e "EQUILANE_DOCKER_PLATFORM=linux/$active_arch" \
            "$image_id" "$@"
    fi
}

capture() {
    echo "Capturing $1" >&2
    run_target "$1" python3 /opt/equilane/docker/native_suite.py capture --target "$1" --output "/results/$1"
}

ensure_comparator() {
    if [ -s "$output/images/$host_role.id" ]; then comparator_role=$host_role
    elif [ -s "$output/images/intel.id" ]; then comparator_role=intel
    else
        comparator_role=$host_role
        build_image "$host_role" || return $?
    fi
}

compare_wrappers() {
    ensure_comparator || { code=$?; record_error compare-image image "$code"; return 2; }
    for target in openpower arm riscv; do
        code=0
        run_target "$comparator_role" python3 /opt/equilane/docker/native_suite.py compare \
            --reference /results/intel --observed "/results/$target" --output "/results/compare-$target.json" || code=$?
        merge_status "$code"
        if [ "$code" -gt 1 ]; then record_error "compare-$target" compare "$code"; fi
    done
}

canonical_pipeline() {
    # The same canonical vectors are decoded by the actual C ABI on every CPU.
    ensure_comparator || return 2
    code=0
    run_target "$comparator_role" equilane generate-vectors --profile "${EQUILANE_PROFILE:-smoke}" \
        --count-per-case "${EQUILANE_COUNT_PER_CASE:-8}" --output /results/vectors || code=$?
    if [ "$code" -ne 0 ]; then record_error canonical-vectors generate "$code"; return 2; fi
    for target in intel openpower arm riscv; do
        code=0
        run_target "$target" equilane run-native --input /results/vectors --role "$target" \
            --output "/results/native/$target" --compiler clang --jobs "$jobs" || code=$?
        merge_status "$code"
        if [ "$code" -gt 1 ]; then record_error "canonical-$target" native "$code"; fi
    done
    for target in openpower arm riscv; do
        code=0
        run_target "$comparator_role" equilane compare-results --input /results/vectors \
            --intel /results/native/intel --target "/results/native/$target" --target-role "$target" \
            --output "/results/canonical/compare-$target" --showcase-report || code=$?
        merge_status "$code"
        if [ "$code" -gt 1 ]; then record_error "canonical-compare-$target" compare "$code"; fi
    done
    if [ "$host_arch" != riscv64 ]; then
        code=0
        EQUILANE_RISCV_CPU=rva23s64,vlen=256 run_target riscv equilane run-native --input /results/vectors --role riscv \
            --output /results/native/riscv-vlen256 --compiler clang --jobs "$jobs" || code=$?
        merge_status "$code"
        if [ "$code" -gt 1 ]; then record_error canonical-riscv-vlen256 native "$code"; fi
        code=0
        run_target "$comparator_role" equilane compare-results --input /results/vectors \
            --intel /results/native/intel --target /results/native/riscv-vlen256 --target-role riscv \
            --output /results/canonical/compare-riscv-vlen256 --showcase-report || code=$?
        merge_status "$code"
        if [ "$code" -gt 1 ]; then record_error canonical-compare-riscv-vlen256 compare "$code"; fi
    fi
}

render_report() {
    ensure_comparator || { fallback_report; return 2; }
    set -- python3 /opt/equilane/docker/native_suite.py report --directory /results
    case "$action" in all|verify) set -- "$@" --canonical ;; esac
    if [ "$action" = report ] && [ -f "$output/vectors/test-vectors.manifest.json" ]; then set -- "$@" --canonical; fi
    if [ "$host_arch" != riscv64 ]; then
        case "$action" in all|verify|wrappers) set -- "$@" --vlen256 ;; esac
    fi
    if [ "$action" = verify ]; then set -- "$@" --tests; fi
    code=0
    run_target "$comparator_role" "$@" || code=$?
    if [ "$code" -gt 1 ] && [ ! -f "$output/pipeline-summary.json" ]; then fallback_report; fi
    merge_status "$code"
}

case "$action" in
    all|verify|wrappers)
        for target in intel openpower arm riscv; do
            code=0
            build_image "$target" || code=$?
            if [ "$code" -ne 0 ]; then record_error "$target-build" build "$code"; continue; fi
            if [ "$action" = verify ]; then
                code=0
                run_target "$target" python3 /opt/equilane/docker/native_suite.py verify-native \
                    --target "$target" --output "/results/native-tests/$target" --jobs "$jobs" || code=$?
                if [ "$code" -ne 0 ]; then record_error "$target-tests" tests "$code"; fi
            fi
            code=0
            capture "$target" || code=$?
            if [ "$code" -ne 0 ]; then record_error "$target-capture" capture "$code"; fi
        done
        if [ "$host_arch" != riscv64 ]; then
            code=0
            EQUILANE_RISCV_CPU=rva23s64,vlen=256 run_target riscv python3 /opt/equilane/docker/native_suite.py capture \
                --target riscv --output /results/riscv-vlen256 || code=$?
            if [ "$code" -ne 0 ]; then record_error riscv-vlen256-capture capture "$code"; fi
        fi
        case "$action" in all|verify) canonical_pipeline || merge_status $? ;; esac
        if [ "$action" = verify ]; then
            code=0
            run_target "$host_role" python3 /opt/equilane/docker/native_suite.py quality --output /results/quality --jobs "$jobs" || code=$?
            merge_status "$code"
            if [ "$code" -gt 1 ]; then record_error quality tests "$code"; fi
        fi
        compare_wrappers || merge_status $?
        render_report ;;
    compare)
        compare_wrappers || merge_status $?
        render_report ;;
    report) render_report ;;
    *)
        code=0
        build_image "$action" || code=$?
        if [ "$code" -eq 0 ]; then capture "$action" || code=$?; fi
        if [ "$code" -ne 0 ]; then record_error "$action-capture" capture "$code"; fallback_report; fi ;;
esac
exit "$status"

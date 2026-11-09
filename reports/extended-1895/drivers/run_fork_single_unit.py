"""Audited artifact driver: original one-unit compiler plus forked case workers."""
import argparse
import hashlib
import json
from pathlib import Path

from equilane import extended
from fork_worker import execute_case_fork


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--role", required=True)
    args = parser.parse_args()
    original_compile = extended._compile
    cache = {}

    def compile_single_unit(catalog, names, directory, role, compiler, attempts, extra_flags=()):
        if not cache:
            extended._compile = original_compile
            try:
                cache.update(original_compile(catalog, [row["name"] for row in catalog["intrinsics"]],
                                              directory, role, compiler, attempts, extra_flags))
            finally:
                extended._compile = compile_single_unit
        return {name: cache[name] for name in names}

    extended._compile = compile_single_unit
    extended._execute_case = execute_case_fork
    result = extended.run_extended_native(args.input, args.output, role=args.role)
    Path(args.output, "execution-layout.json").write_text(json.dumps({
        "schema_version": 1,
        "purpose": "One compilation unit with original 180-second/bisection policy; each case runs in a fresh fork using the unchanged committed _worker_main and result parser.",
        "case_isolation": "os.fork with stdout pipe; per-input SIGALRM and original parent deadline retained",
        "stdout_capture": "file descriptor 1, Python stream, os.write, and libc stdout; stderr uses descriptor 2",
        "new_python_interpreter_per_case": False,
        "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "fork_worker_sha256": hashlib.sha256(Path(__file__).with_name("fork_worker.py").read_bytes()).hexdigest(),
        "source_commit": "e3092c451238d77efe241d91a719df0c51ed993a",
    }, indent=2) + "\n")
    print(json.dumps(result))
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())

"""Artifact-only process-launch adapter; committed worker/row checks stay intact."""
import io
import ctypes
import os
import selectors
import signal
import subprocess
import sys
import tempfile
import time
import traceback

from equilane import extended

ORIGINAL_RUN = subprocess.run
ORIGINAL_EXECUTE_CASE = extended._execute_case


def fork_worker_run(command, **kwargs):
    if list(command[-3:]) != ["-m", "equilane.extended", "_worker"]:
        return ORIGINAL_RUN(command, **kwargs)
    if kwargs.get("text") is not True or kwargs.get("capture_output") is not True:
        raise ValueError("fork adapter requires the original text/capture-output worker contract")
    request = kwargs["input"]
    timeout = kwargs["timeout"]
    read_fd, write_fd = os.pipe()
    sys.stdout.flush()
    sys.stderr.flush()
    with tempfile.TemporaryFile(mode="w+b") as stderr_file:
        pid = os.fork()
        if pid == 0:
            os.close(read_fd)
            sys.stdin = io.StringIO(request)
            os.dup2(write_fd, 1)
            if write_fd != 1:
                os.close(write_fd)
            sys.stdout = open(1, "w", encoding="utf-8", buffering=1, closefd=False)
            os.dup2(stderr_file.fileno(), 2)
            sys.stderr = open(2, "w", encoding="utf-8", buffering=1, closefd=False)
            code = 0
            try:
                extended._worker_main()
            except BaseException:
                traceback.print_exc()
                code = 1
            finally:
                try:
                    sys.stdout.flush()
                    # os._exit does not flush libc; normal subprocess exit does.
                    fflush = ctypes.CDLL(None).fflush
                    fflush.argtypes = [ctypes.c_void_p]
                    fflush.restype = ctypes.c_int
                    fflush(None)
                    sys.stderr.flush()
                except BaseException:
                    code = 1
                os._exit(code)
        os.close(write_fd)
        os.set_blocking(read_fd, False)
        selector = selectors.DefaultSelector()
        selector.register(read_fd, selectors.EVENT_READ)
        chunks, status, eof, timed_out = [], None, False, False
        deadline = time.monotonic() + timeout
        try:
            while not (eof and status is not None):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    if status is None:
                        os.kill(pid, signal.SIGKILL)
                        _, status = os.waitpid(pid, 0)
                    timed_out = True
                    # Preserve bytes already written without waiting on an
                    # inherited pipe descriptor after the worker deadline.
                    while not eof:
                        try:
                            chunk = os.read(read_fd, 65536)
                        except BlockingIOError:
                            break
                        if not chunk:
                            eof = True
                        else:
                            chunks.append(chunk)
                    break
                for key, _events in selector.select(0.05 if status is not None else max(0, min(0.05, remaining))):
                    try:
                        chunk = os.read(key.fd, 65536)
                    except BlockingIOError:
                        continue
                    if chunk:
                        chunks.append(chunk)
                    else:
                        eof = True
                        selector.unregister(read_fd)
                if status is None:
                    child, child_status = os.waitpid(pid, os.WNOHANG)
                    if child:
                        status = child_status
        finally:
            selector.close()
            os.close(read_fd)
            if status is None:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                os.waitpid(pid, 0)
        stdout_bytes = b"".join(chunks)
        stderr_file.seek(0)
        stderr = stderr_file.read().decode("utf-8")
        if timed_out:
            raise subprocess.TimeoutExpired(command, timeout, output=stdout_bytes, stderr=stderr)
        return subprocess.CompletedProcess(command, os.waitstatus_to_exitcode(status),
                                           stdout_bytes.decode("utf-8"), stderr)


def execute_case_fork(*args, **kwargs):
    previous = extended.subprocess.run
    extended.subprocess.run = fork_worker_run
    try:
        # Includes the original library digest, row ID/count and signal checks.
        return ORIGINAL_EXECUTE_CASE(*args, **kwargs)
    finally:
        extended.subprocess.run = previous

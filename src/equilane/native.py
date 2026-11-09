"""Linux native ABI runner, isolated calls, and authenticated build evidence.

No portable model supplies an observed value. The worker calls the compiled
case symbol and the existing artifact validator checks the completion marker.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import ctypes as ct
import json
import math
import multiprocessing as mp
from multiprocessing.connection import wait
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
import time
from typing import Callable

from .artifacts import InputArtifact, ResultArtifact, validate_result_artifact
from .canonical import atomic_write, dump_bytes, loads, remove_completion_marker, sha256_bytes, utf16_sort_key, write_jsonl
from .cases import CaseRegistry
from .errors import RunnerError, UnsupportedError, ValidationError
from .isa import ISARegistry, project_used_isa
from .records import FP_EXCEPTION_ORDER, validate_input_record, validate_result_record
from .targets import FP_CONTROL_KEYS, TARGETS


class InputBytes(ct.Structure):
    _fields_ = [("data", ct.c_void_p), ("size", ct.c_uint64)]


class ABIInput(ct.Structure):
    _fields_ = [("abi_version", ct.c_uint32), ("struct_size", ct.c_uint32),
                ("encoded_arguments", InputBytes), ("rounding_mode", ct.c_uint32), ("fp_mode", ct.c_uint32)]


class ABIOutput(ct.Structure):
    _fields_ = [("abi_version", ct.c_uint32), ("struct_size", ct.c_uint32),
                ("encoded_return_value", ct.c_void_p), ("return_capacity", ct.c_uint64), ("return_size", ct.c_uint64),
                ("encoded_memory_effects", ct.c_void_p), ("effects_capacity", ct.c_uint64), ("effects_size", ct.c_uint64),
                ("normalized_fp_exceptions", ct.c_uint32), ("status", ct.c_uint32)]


ROUNDINGS = {"nearest_even": 0, "toward_zero": 1, "toward_positive": 2, "toward_negative": 3}


@dataclass(frozen=True)
class NativeRunResult:
    output_directory: Path
    manifest_path: Path
    results_path: Path
    record_count: int
    sha256: str
    manifest: dict
    records: tuple[dict, ...]

    def as_validated_artifact(self) -> ResultArtifact:
        return ResultArtifact(self.manifest_path, self.results_path, self.manifest,
                              {str(r["input_id"]): r for r in self.records}, self.records,
                              str(self.manifest["runner"]["role"]), False, self.record_count, self.sha256, True)


def _run(command, log: Path, *, timeout=300):
    try:
        result = subprocess.run([str(item) for item in command], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RunnerError(f"native command could not complete: {command[0]}: {exc}") from exc
    with log.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps([str(item) for item in command])+"\n"+result.stdout+result.stderr)
    if result.returncode:
        raise RunnerError(f"native command failed ({result.returncode}): {command[0]}\n{result.stderr[-4000:]}")
    return result.stdout


def _root(cases):
    candidates = [Path(os.environ.get("EQUILANE_PROJECT_ROOT", Path.cwd()))]
    candidates += [case.source_path.parents[2] for case in cases
                   if case.source_path is not None and len(case.source_path.parents) > 2]
    for candidate in candidates:
        if (candidate/"include/framework/case_abi.h").is_file() and (candidate/"adapters/common/native_preflight.c").is_file():
            return candidate.resolve()
    raise RunnerError("native build requires the trusted EquiLane source checkout")


def _commit(root):
    value = os.environ.get("EQUILANE_GIT_COMMIT", "")
    if not value:
        result = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True)
        value = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value):
        raise RunnerError("native build needs a real source commit (EQUILANE_GIT_COMMIT in Docker)")
    return value


def _contract_digest(case, role):
    # Other architectures' optional bindings do not change this role's ABI.
    payload={key:value for key,value in case.data.items() if key not in TARGETS or key==role}
    return sha256_bytes(dump_bytes(payload,newline=True))


def build_native(cases: CaseRegistry, role: str, output: Path, *, compiler="clang"):
    from .native_codegen import generate_native_suite, native_compile_flags
    root = _root(cases)
    output.mkdir(parents=True, exist_ok=True)
    log = output/"build.log"
    log.write_text("")
    generated = generate_native_suite(cases, role, output, project_root=root)
    version = _run([compiler,"--version"],log).splitlines()[0]
    if "clang" not in version.lower():
        raise UnsupportedError("the native runner requires Clang with strict floating-point support")
    triple = _run([compiler,"-dumpmachine"],log).strip()
    options = ["-O2","-std=gnu11","-fPIC","-ffp-model=strict", *native_compile_flags(role),
               "-I"+str(root), "-I"+str(root/"include"), "-I"+str(root/"adapters/common")]
    compiler_info = {"name":"clang","target_triple":triple,"version":version}
    sources = [("adapter.encoded", "adapter", generated),
               ("support.runtime","support",root/"adapters/common/native_suite_runtime.c"),
               ("support.preflight","support",root/"adapters/common/native_preflight.c")]
    sources += [("sut."+block,"sut",root/"10_official_suite"/role/(block+".c"))
                for block in ("f32x4","f64x2","i8x16","i16x8","i32x4","i64x2")]
    units, objects, source_files = [], [], {}
    for unit_id, kind, source in sources:
        destination = output/(unit_id+".o")
        blob = _run([compiler,*options,"-E","-P",source],log)
        macros_text = _run([compiler,*options,"-dM","-E",source],log)
        macros = {}
        for line in macros_text.splitlines():
            match = re.fullmatch(r"#define ([A-Za-z_][A-Za-z0-9_]*)(?: (.*))?",line)
            if match: macros[match[1]]=match[2] or ""
        _run([compiler,*options,"-c",source,"-o",destination],log)
        units.append({"id":unit_id,"kind":kind,"assertions_enabled":"NDEBUG" not in macros,
                      "compile_options":options,"compiler":compiler_info,"feature_macros":macros,
                      "source_blob_sha256":sha256_bytes(blob.encode()),
                      "object_sha256":sha256_bytes(destination.read_bytes())})
        source_files[str(source)]=sha256_bytes(source.read_bytes())
        objects.append(destination)
    headers=list((root/"include").rglob("*.h"))+list((root/"adapters/common").glob("*.h"))
    headers+=list((root/"10_official_suite"/role).glob("*.h"))+[root/"10_official_suite/shortcuts.h",
                                                                root/"src/equilane/native_codegen.py"]
    for path in headers:
        source_files[str(path)]=sha256_bytes(path.read_bytes())
    library = output/("libequilane_"+role+".so")
    link_options=["-shared","-Wl,-z,defs","-lm"]
    _run([compiler,*objects,*link_options,"-o",library],log)
    symbols={case.id:str(case.data[role]["symbol"]) for case in cases if role in case.target_roles}
    exported = _run(["nm","-D","--defined-only",library],log)
    available_symbols={line.split()[-1] for line in exported.splitlines() if line.split()}
    if set(symbols.values())-available_symbols:
        raise RunnerError("compiled library is missing registered ABI symbols")
    evidence={"schema_version":1,"artifact_type":"equilane.native-build","role":role,
              "library":str(library.resolve()),"binary_sha256":sha256_bytes(library.read_bytes()),
              "git_commit":_commit(root),"build_units":sorted(units,key=lambda u:u["id"]),
              "link_options":link_options,"symbols":symbols,"source_files":source_files,
              "case_contract_sha256":{case.id:_contract_digest(case,role) for case in cases if role in case.target_roles},
              "case_operations":{case.id:case.source_path.stem for case in cases if role in case.target_roles},
              "source_root":str(root),"symbol_audit":{"registered":len(symbols),"missing":[]}}
    atomic_write(output/"build-evidence.json",dump_bytes(evidence,newline=True))
    return library,evidence


def _load_build(library, cases, role):
    library=Path(library).resolve()
    try: evidence=json.loads((library.parent/"build-evidence.json").read_bytes())
    except (OSError,ValueError) as exc: raise ValidationError("native library has invalid build evidence") from exc
    if (not isinstance(evidence,dict) or evidence.get("artifact_type")!="equilane.native-build"
        or evidence.get("role")!=role or evidence.get("binary_sha256")!=sha256_bytes(library.read_bytes())):
        raise ValidationError("native library build evidence is missing or does not authenticate this library")
    symbols=evidence.get("symbols",{})
    for case in cases:
        if role in case.target_roles and symbols.get(case.id)!=case.data[role]["symbol"]:
            raise ValidationError("native library does not cover the authenticated case contract")
        if role in case.target_roles and (evidence.get("case_contract_sha256",{}).get(case.id)!=_contract_digest(case,role)
            or evidence.get("case_operations",{}).get(case.id)!=(case.source_path.stem if case.source_path else None)):
            raise ValidationError("native library case contract differs; rebuild before using it")
    if not isinstance(evidence.get("source_files"),dict) or not evidence["source_files"]:
        raise ValidationError("native library is missing source evidence")
    for path,digest in evidence["source_files"].items():
        if sha256_bytes(Path(path).read_bytes())!=digest:
            raise ValidationError("native library source snapshot differs; rebuild before using it")
    return library,evidence


def _probe_process(library):
    lib=ct.CDLL(str(library),mode=os.RTLD_LOCAL)
    function=lib.equilane_preflight
    function.argtypes=[ct.c_void_p,ct.c_size_t]; function.restype=ct.c_int
    buffer=ct.create_string_buffer(8192)
    code=function(buffer,len(buffer))
    if code: raise RunnerError(f"native CPU/FP behavioral preflight failed: {code}")
    observation=json.loads(buffer.value)
    libraries=[]
    for line in Path("/proc/self/maps").read_text().splitlines():
        fields=line.split()
        if len(fields)<6 or not fields[-1].startswith("/"): continue
        path=Path(fields[-1]).resolve()
        if (".so" in path.name or "ld-linux" in path.name) and path.is_file():
            libraries.append(str(path))
    observation["loaded_libraries"]=[{"path":path,"sha256":sha256_bytes(Path(path).read_bytes())}
                                      for path in sorted(set(libraries),key=utf16_sort_key)]
    print(json.dumps(observation))


def _preflight(library, output, timeout):
    try:
        result=subprocess.run([sys.executable,"-m","equilane.native","--probe",str(library)],
                              capture_output=True,text=True,timeout=max(30,timeout))
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise RunnerError(f"native preflight worker failed: {exc}") from exc
    atomic_write(output/"preflight.log",(result.stdout+result.stderr).encode())
    if result.returncode:
        raise UnsupportedError(f"CPU/FP preflight did not pass ({result.returncode}): {result.stderr[-1500:]}")
    try: observation=json.loads(result.stdout)
    except ValueError as exc: raise RunnerError("native preflight emitted invalid JSON") from exc
    if not isinstance(observation,dict): raise RunnerError("native preflight emitted a non-object result")
    if observation.get("status")!="passed": raise RunnerError("incomplete CPU/FP preflight")
    required={"status","available_isa","boolean_controls","detector","fp_exception_flags","rounding_modes","loaded_libraries"}
    if set(observation)!=required: raise RunnerError("native preflight is missing measured observations")
    if not isinstance(observation["detector"],str) or not observation["detector"]:
        raise RunnerError("native preflight is missing its CPU detector identity")
    controls=observation["boolean_controls"]
    if not isinstance(controls,dict) or not controls or any(type(value)is not bool for value in controls.values()):
        raise RunnerError("native preflight has invalid FP control readbacks")
    for key in ("available_isa","rounding_modes","fp_exception_flags"):
        values=observation[key]
        if not isinstance(values,list) or not values or any(not isinstance(v,str) or not v for v in values) or len(set(values))!=len(values):
            raise RunnerError("native preflight has invalid "+key)
    if observation["fp_exception_flags"]!=FP_EXCEPTION_ORDER or set(observation["rounding_modes"])!=set(ROUNDINGS):
        raise RunnerError("native preflight did not measure every rounding mode and FP exception")
    libraries=observation["loaded_libraries"]
    if not isinstance(libraries,list) or not libraries: raise RunnerError("native preflight is missing loaded library evidence")
    for item in libraries:
        if (not isinstance(item,dict) or set(item)!={"path","sha256"} or not isinstance(item["path"],str)
            or not item["path"].startswith("/") or not isinstance(item["sha256"],str)
            or not re.fullmatch(r"[0-9a-f]{64}",item["sha256"])):
            raise RunnerError("native preflight has invalid loaded library evidence")
    atomic_write(output/"preflight-observations.json",dump_bytes(observation,newline=True))
    return observation


def _error(record,role,status,code):
    stages={"infrastructure_error":"runner","runtime_error":"execution","signal":"signal",
            "unsupported":"capability","invalid_input":"input_validation"}
    return {"schema_version":1,"case_id":record["case_id"],"input_id":record["input_id"],
            "runner":role,"duration_ns":0,"status":status,"error":{"code":code,"stage":stages[status]}}


def _call(function, case, record, role):
    payload={key:record.get(key,{}) for key in ("buffers","immediates","operands")}
    raw=dump_bytes(payload)
    encoded=ct.create_string_buffer(raw)
    argument=ABIInput(1,ct.sizeof(ABIInput),InputBytes(ct.cast(encoded,ct.c_void_p),len(raw)),
                      ROUNDINGS[record["environment"]["rounding"]],0)
    effects_size=max(8192,sum(len(v["bytes"]) for v in record.get("buffers",{}).values())+8192)
    returned=ct.create_string_buffer(8192); effects=ct.create_string_buffer(effects_size)
    output=ABIOutput(1,ct.sizeof(ABIOutput),ct.cast(returned,ct.c_void_p),len(returned),0,
                     ct.cast(effects,ct.c_void_p),len(effects),0,0,3)
    start=time.perf_counter_ns()
    call_status=function(ct.byref(argument),ct.byref(output))
    elapsed=time.perf_counter_ns()-start
    if call_status:
        result=_error(record,role,"infrastructure_error","abi_call_"+str(call_status))
    elif output.status:
        status={1:"unsupported",2:"invalid_input",3:"runtime_error"}.get(output.status,"runtime_error")
        result=_error(record,role,status,"abi_output_"+str(output.status))
    else:
        if output.return_size>len(returned) or output.effects_size>len(effects):
            raise RunnerError("ABI output size exceeds negotiated capacity")
        observed={}
        if case.signature["return"]["type"]!="void":
            observed["return"]=loads(returned.raw[:output.return_size])
        if any(a["type"]=="pointer" for a in case.signature["arguments"]):
            observed["buffers"]=loads(effects.raw[:output.effects_size])
        if case.environment["observe_fp_exceptions"]:
            if output.normalized_fp_exceptions & ~31: raise RunnerError("unknown ABI FP exception bit")
            observed["fp_exceptions"]=[name for i,name in enumerate(FP_EXCEPTION_ORDER)
                                       if output.normalized_fp_exceptions&(1<<i)]
        result={"schema_version":1,"case_id":record["case_id"],"input_id":record["input_id"],
                "runner":role,"duration_ns":elapsed,"status":"ok","observed":observed}
    result["duration_ns"]=elapsed
    validate_result_record(result,case,role=role,input_record=record)
    return result


def _batch(library,case,records,role,connection):
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CORE,(0,0))
        lib=ct.CDLL(str(library),mode=os.RTLD_LOCAL)
        setup=lib.equilane_ieee_setup; setup.restype=ct.c_int
        if not setup(): raise RunnerError("worker could not enter IEEE FP mode")
        function=getattr(lib,str(case.data[role]["symbol"]))
        function.argtypes=[ct.POINTER(ABIInput),ct.POINTER(ABIOutput)]; function.restype=ct.c_int
        for record in records:
            connection.send(("started",record["input_id"]))
            try: result=_call(function,case,record,role)
            except Exception: result=_error(record,role,"infrastructure_error","invalid_worker_output")
            connection.send(("result",result))
    except Exception as exc:
        connection.send(("worker_error",str(exc)))
    finally: connection.close()


def _execute(library, cases, records, role, timeout, jobs, progress):
    groups={}
    for record in records: groups.setdefault(str(record["case_id"]),[]).append(record)
    pending=deque((cases.get(case_id),batch) for case_id,batch in groups.items())
    context=mp.get_context("fork")
    active={}; results={}
    def store(record):
        results[str(record["input_id"])]=record
        if progress: progress(len(results))
    try:
        while pending or active:
            while pending and len(active)<jobs:
                case,batch=pending.popleft()
                if role not in case.target_roles:
                    for record in batch: store(_error(record,role,"unsupported","target_binding_unavailable"))
                    continue
                parent,child=context.Pipe(duplex=False)
                process=context.Process(target=_batch,args=(library,case,batch,role,child))
                process.start(); child.close()
                active[parent]={"process":process,"case":case,"batch":batch,"current":None,
                                "started":time.monotonic(),"done":set()}
            ready=wait(list(active),timeout=0.05) if active else []
            for connection in ready:
                state=active[connection]
                try: kind,value=connection.recv()
                except EOFError: kind,value="eof",None
                if kind=="started": state["current"]=value; state["started"]=time.monotonic()
                elif kind=="result":
                    input_id=str(value["input_id"])
                    expected=next((r for r in state["batch"] if r["input_id"]==input_id),None)
                    if expected is None or input_id in state["done"]: raise RunnerError("worker input identity changed")
                    validate_result_record(value,state["case"],role=role,input_record=expected)
                    state["done"].add(input_id); state["current"]=None; store(value)
                elif kind in {"eof","worker_error"}: state["closed"]=True
            for connection,state in list(active.items()):
                process=state["process"]
                timed_out=time.monotonic()-state["started"]>timeout
                if not timed_out and not state.get("closed") and process.is_alive(): continue
                # Drain queued results before interpreting a normal process exit.
                if not timed_out and not state.get("closed") and connection.poll(): continue
                if timed_out and process.is_alive(): process.kill()
                process.join(timeout=1)
                if process.is_alive(): process.kill(); process.join()
                remaining=[r for r in state["batch"] if r["input_id"] not in state["done"]]
                if remaining:
                    current=next((r for r in remaining if r["input_id"]==state["current"]),remaining[0])
                    if timed_out: status,code="runtime_error","execution_timeout"
                    elif process.exitcode is not None and process.exitcode<0:
                        status,code="signal","signal_"+str(-process.exitcode)
                    else: status,code="infrastructure_error","worker_exit"
                    store(_error(current,role,status,code))
                    rest=[r for r in remaining if r is not current]
                    if rest: pending.appendleft((state["case"],rest))
                connection.close(); del active[connection]
    finally:
        for connection,state in active.items():
            if state["process"].is_alive(): state["process"].kill()
            state["process"].join(); connection.close()
    return [results[str(record["input_id"])] for record in records]


def run_native(*, input_artifact: InputArtifact, cases: CaseRegistry, isa_registry: ISARegistry,
               role: str, output: Path, library=None, compiler="clang", timeout=5.0, jobs=1,
               progress: Callable[[int],None] | None=None) -> NativeRunResult:
    if role not in TARGETS: raise ValidationError("unknown native target role")
    if type(jobs) is not int or jobs<1: raise ValidationError("native jobs must be a positive integer")
    if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not math.isfinite(timeout) or timeout<=0:
        raise ValidationError("native timeout must be a finite positive number")
    architecture={"arm64":"aarch64","amd64":"x86_64","powerpc64le":"ppc64le"}.get(platform.machine(),platform.machine())
    if platform.system()!="Linux" or architecture!=TARGETS[role].architecture or sys.byteorder!="little" or ct.sizeof(ct.c_long)!=8:
        raise UnsupportedError(f"native {role} requires Linux/{TARGETS[role].architecture} LP64 little-endian; use its Docker platform")
    records=tuple(input_artifact.records)
    if not records: raise ValidationError("native input artifact is empty")
    for record in records: validate_input_record(record,cases.get(str(record["case_id"])))
    output=Path(output).resolve(); output.mkdir(parents=True,exist_ok=True)
    manifest_path=output/TARGETS[role].manifest_file
    remove_completion_marker(manifest_path)
    remove_completion_marker(output/"native-traceability.json")
    if library is None: library,evidence=build_native(cases,role,output/"build",compiler=compiler)
    else: library,evidence=_load_build(library,cases,role)
    evidence_bytes=dump_bytes(evidence,newline=True)
    atomic_write(output/"native-build-evidence.json",evidence_bytes)
    observation=_preflight(library,output,timeout)
    controls=observation["boolean_controls"]
    if set(controls)!=set(FP_CONTROL_KEYS[architecture]) or any(type(v)is not bool or v for v in controls.values()):
        raise UnsupportedError("native hardware FP controls do not match IEEE mode")
    available=observation["available_isa"]
    for case in cases:
        if role in case.target_roles and not set(isa_registry.closure(case.required_isa(role),architecture=architecture)).issubset(available):
            raise UnsupportedError("native instruction requirements did not pass the CPU probe")
    roundings=sorted({r["environment"]["rounding"] for r in records},key=utf16_sort_key)
    if not set(roundings).issubset(observation["rounding_modes"]): raise UnsupportedError("native rounding modes unavailable")
    results=_execute(library,cases,records,role,float(timeout),min(jobs,len(cases)),progress)
    results_path=output/TARGETS[role].results_file
    count,length,digest=write_jsonl(results_path,results)
    ids={str(r["case_id"]) for r in records}
    used=project_used_isa(isa_registry,[cases.get(i) for i in sorted(ids,key=utf16_sort_key)])
    probe_id="native-controls.v1"
    probe_suite=[{"id":probe_id,"version":1,"implementation_id":"equilane.native.behavioral.v1",
                  "architecture":architecture,"expected":{"boolean_controls":controls,
                  "rounding_modes":observation["rounding_modes"],"fp_exception_flags":observation["fp_exception_flags"]}}]
    unit_ids=sorted([u["id"] for u in evidence["build_units"]],key=utf16_sort_key)
    environment={"architecture":architecture,"os":"linux","endianness":"little",
                 "cpu_model":os.environ.get("QEMU_CPU") or platform.processor() or architecture,
                 "kernel":platform.release(),"git_commit":evidence["git_commit"],
                 "assertions_enabled":True,"available_isa":available,"fp_rounding_modes":roundings,
                 "fp_controls":controls,"build_units":evidence["build_units"],
                 "case_build_units":{i:unit_ids for i in sorted(ids,key=utf16_sort_key)},
                 "link":{"binary_sha256":evidence["binary_sha256"],"link_options":evidence["link_options"],
                         "loaded_libraries":observation["loaded_libraries"]}}
    if role=="openpower": environment["vector_semantics"]={"altivec_src_compat":"gcc","element_reg_order":"little"}
    manifest={"schema_version":1,"artifact_type":"ioitf.runner-results","complete":True,
              "case_definitions_sha256":cases.projected_sha256(ids),"input_sha256":input_artifact.sha256,
              "isa_registry_sha256":isa_registry.sha256,"used_isa_contract_sha256":used.sha256,
              "runner":{"abi_version":1,"role":role,"build_id":"equilane-native:"+role+":"+evidence["binary_sha256"]},
              "environment":environment,"preflight":{"probe_suite":probe_suite,
                "probe_suite_sha256":sha256_bytes(dump_bytes(probe_suite,newline=True)),
                "probes":[{"id":probe_id,"status":"passed"}],"status":"passed"},
              "results":{"file":results_path.name,"record_count":count,"byte_length":length,"sha256":digest}}
    atomic_write(manifest_path,dump_bytes(manifest,newline=True))
    try: validate_result_artifact(manifest_path,cases,isa_registry,input_artifact=input_artifact)
    except Exception:
        remove_completion_marker(manifest_path); raise
    trace={"schema_version":1,"artifact_type":"equilane.native-traceability","complete":True,
           "input_sha256":input_artifact.sha256,"result_sha256":digest,"library_sha256":evidence["binary_sha256"],
           "source_commit":evidence["git_commit"],"symbol_audit":evidence["symbol_audit"],
           "build_evidence":{"file":"native-build-evidence.json","sha256":sha256_bytes(evidence_bytes)},
           "preflight_observations":{"file":"preflight-observations.json",
                "sha256":sha256_bytes((output/"preflight-observations.json").read_bytes())},
           "execution":{"mode":os.environ.get("EQUILANE_EXECUTION_MODE","native-local"),
                        "docker_image_id":os.environ.get("EQUILANE_IMAGE_ID"),
                        "host_architecture":os.environ.get("EQUILANE_HOST_ARCH"),"qemu_cpu":os.environ.get("QEMU_CPU"),
                        "process_isolation":"case-worker-restarted-after-signal-or-timeout","timeout_ms":int(timeout*1000)},
           "cases":{i:{"symbol":evidence["symbols"].get(i),"build_units":unit_ids,
                       "contract_sha256":_contract_digest(cases.get(i),role),
                       "operation":cases.get(i).source_path.stem if cases.get(i).source_path else None,
                       "input_ids_sha256":sha256_bytes(dump_bytes([r["input_id"] for r in records if r["case_id"]==i])),
                       "inputs":sum(r["case_id"]==i for r in records)} for i in sorted(ids)}}
    atomic_write(output/"native-traceability.json",dump_bytes(trace,newline=True))
    from .native_traceability import validate_traceability
    try:
        validate_traceability(output,input_artifact=input_artifact,cases=cases,isa_registry=isa_registry,role=role)
    except Exception:
        remove_completion_marker(manifest_path)
        remove_completion_marker(output/"native-traceability.json")
        raise
    return NativeRunResult(output,manifest_path,results_path,count,digest,manifest,tuple(results))


if __name__=="__main__":
    if len(sys.argv)==3 and sys.argv[1]=="--probe":
        _probe_process(Path(sys.argv[2]))
    else: raise SystemExit("internal native worker: --probe LIBRARY")

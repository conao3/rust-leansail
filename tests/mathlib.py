import argparse
import hashlib
import json
import math
import os
import platform
import shutil
import signal
import statistics
import subprocess
import threading
import time
from pathlib import Path

from client import Client

ROOT = Path(__file__).resolve().parents[1]
REVISION = "56a005855ea17e0b0958f73ec801ae140b0a1679"
FILES = [
    "Mathlib/MeasureTheory/Integral/IntegralEqImproper.lean",
    "Mathlib/Topology/Algebra/Module/FiniteDimension.lean",
    "Mathlib/Tactic.lean",
]
METHODS = [
    "textDocument/hover",
    "textDocument/definition",
    "textDocument/references",
    "textDocument/documentHighlight",
    "textDocument/semanticTokens/full",
    "textDocument/completion",
    "$/lean/plainGoal",
]


def run(command, cwd, **kwargs):
    return subprocess.run(command, cwd=cwd, check=True, text=True, **kwargs)


def prepare(workspace):
    environment = dict(os.environ, MATHLIB_NO_CACHE_ON_UPDATE="1")
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "lakefile.toml").write_text(
        'name = "leansail-mathlib-bench"\nversion = "0.1.0"\n\n'
        '[[require]]\nname = "mathlib"\n'
        'git = "https://github.com/leanprover-community/mathlib4.git"\n'
        f'rev = "{REVISION}"\n'
    )
    (workspace / "lean-toolchain").write_text("leanprover/lean4:v4.35.0\n")
    run(
        ["lake", "update"],
        workspace,
        env=environment,
    )
    run(
        ["lake", "exe", "cache", "get"],
        workspace,
        env=environment,
    )
    (workspace / "Probe.lean").write_text(
        "import Mathlib.MeasureTheory.Integral.IntegralEqImproper\n"
        "import Mathlib.Topology.Algebra.Module.FiniteDimension\n"
        "import Mathlib.Tactic\n\nexample (n : ℕ) : n = n := by rfl\n"
    )
    run(["lake", "env", "lean", "Probe.lean"], workspace)
    for relative in FILES:
        run(
            [
                "lake",
                "setup-file",
                str(workspace / ".lake/packages/mathlib" / relative),
            ],
            workspace,
            stdout=subprocess.DEVNULL,
        )


def build_backend():
    directory = ROOT / "target/mathlib-backend"
    directory.mkdir(parents=True, exist_ok=True)
    for name in ["Main.lean", "lakefile.toml"]:
        source = ROOT / "native" / name
        target = directory / name
        if not target.exists() or target.read_bytes() != source.read_bytes():
            shutil.copyfile(source, target)
    run(["lake", "build"], directory)
    source_directory = directory / ".lake/build/src"
    source_directory.mkdir(parents=True, exist_ok=True)
    source_link = source_directory / "lean"
    if source_link.is_symlink():
        source_link.unlink()
    prefix = run(
        ["lean", "--print-prefix"], directory, capture_output=True
    ).stdout.strip()
    source_link.symlink_to(Path(prefix) / "src/lean", target_is_directory=True)
    return directory / ".lake/build/bin/leansail-backend"


def processes():
    result = {}
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            pid = int(path.parent.name)
            stat = path.read_text().rsplit(")", 1)[1].split()
            result[pid] = {
                "ppid": int(stat[1]),
                "ticks": int(stat[11]) + int(stat[12]),
                "rss_kib": int(stat[21]) * os.sysconf("SC_PAGE_SIZE") // 1024,
            }
        except (OSError, ValueError, IndexError):
            continue
    return result


def descendants(table, root):
    found = {root}
    while True:
        added = {pid for pid, p in table.items() if p["ppid"] in found} - found
        if not added:
            return found
        found |= added


def outside_cpu(before, after, exclude, seconds):
    return (
        sum(
            max(0, p["ticks"] - before.get(pid, {"ticks": 0})["ticks"])
            for pid, p in after.items()
            if pid not in exclude
        )
        / os.sysconf("SC_CLK_TCK")
        / seconds
    )


class Monitor:
    def __init__(self, pid, pss=False):
        self.pid = pid
        self.pss = pss
        self.stop = threading.Event()
        self.peaks = {}
        self.total_peak_kib = 0
        self.total_peak_pss_kib = 0
        self.pss_samples = []
        self.error = None
        self.outside_ticks = 0
        self.before = processes()
        self.started = time.monotonic()
        self.thread = threading.Thread(target=self.sample, daemon=True)
        self.thread.start()

    def sample(self):
        try:
            self.sample_loop()
        except Exception as error:
            self.error = repr(error)
            raise

    def sample_loop(self):
        before = self.before
        while not self.stop.wait(0.1):
            table = processes()
            owned = descendants(table, self.pid)
            self.outside_ticks += sum(
                max(0, p["ticks"] - before[pid]["ticks"])
                for pid, p in table.items()
                if pid in before and pid not in owned and pid != os.getpid()
            )
            total = 0
            total_pss = 0
            sample_pss = {}
            for pid in owned & table.keys():
                p = table[pid]
                total += p["rss_kib"]
                if pid not in self.peaks:
                    try:
                        command = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
                    except OSError:
                        continue
                    self.peaks[pid] = {
                        "pid": pid,
                        "ppid": p["ppid"],
                        "command": [x.decode(errors="replace") for x in command if x],
                        "peak_rss_kib": 0,
                        "cpu_ticks": 0,
                    }
                self.peaks[pid]["peak_rss_kib"] = max(
                    self.peaks[pid]["peak_rss_kib"], p["rss_kib"]
                )
                self.peaks[pid]["cpu_ticks"] = p["ticks"]
                if self.pss:
                    try:
                        fields = (
                            Path(f"/proc/{pid}/smaps_rollup").read_text().splitlines()
                        )
                    except ProcessLookupError:
                        continue
                    except FileNotFoundError:
                        continue
                    pss = int(
                        next(
                            line.split()[1]
                            for line in fields
                            if line.startswith("Pss:")
                        )
                    )
                    sample_pss[str(pid)] = pss
                    total_pss += pss
                    self.peaks[pid]["peak_pss_kib"] = max(
                        self.peaks[pid].get("peak_pss_kib", 0), pss
                    )
            self.total_peak_kib = max(self.total_peak_kib, total)
            if self.pss:
                self.total_peak_pss_kib = max(self.total_peak_pss_kib, total_pss)
                self.pss_samples.append(
                    {
                        "elapsed_s": time.monotonic() - self.started,
                        "process_pss_kib": sample_pss,
                        "total_pss_kib": total_pss,
                    }
                )
            before = table

    def finish(self):
        self.stop.set()
        self.thread.join()
        return {
            "sample_interval_ms": 100,
            "sampling_error": self.error,
            "processes": list(self.peaks.values()),
            "simultaneous_peak_rss_kib": self.total_peak_kib,
            **(
                {
                    "simultaneous_peak_pss_kib": self.total_peak_pss_kib,
                    "pss_samples": self.pss_samples,
                }
                if self.pss
                else {}
            ),
            "outside_cpu_cores": self.outside_ticks
            / os.sysconf("SC_CLK_TCK")
            / (time.monotonic() - self.started),
        }


def stats(values):
    return {
        "median_ms": statistics.median(values),
        "p95_ms": sorted(values)[math.ceil(len(values) * 0.95) - 1],
        "samples_ms": values,
    }


def position(text, offset):
    prefix = text[:offset]
    return {
        "line": prefix.count("\n"),
        "character": len(prefix.rsplit("\n", 1)[-1].encode("utf-16-le")) // 2,
    }


def fixture(path):
    original = path.read_text()
    suffix = (
        "\nnamespace LeansailBenchmark\n"
        "def twice (n : Nat) : Nat := n + n\n"
        "example (n : Nat) : twice n = n + n := by\n"
        "  rfl\n"
        "#check twice\n"
        "end LeansailBenchmark\n"
    )
    text = original + suffix
    point = position(text, text.rindex("#check twice") + len("#check tw"))
    goal = position(text, text.rindex("  rfl") + 2)
    proof_offset = original.find("by\n")
    early = (
        position(text, proof_offset + 2)
        if proof_offset >= 0
        else position(text, len(original))
    )
    return text, point, goal, early


def session(args, label, command, repetition):
    environment = dict(os.environ)
    environment.pop("LEAN_WORKER_PATH", None)
    if args.profile:
        environment["LEAN_LS_PROFILE"] = str(
            (args.output.parent / f"{label}-{repetition}").resolve()
        )
    started = time.perf_counter_ns()
    client = Client(command, env=environment)
    monitor = Monitor(client.process.pid)
    result = {
        "server": label,
        "repetition": repetition,
        "command": command,
        "files": [],
    }

    def request(method, params):
        start = time.perf_counter_ns()
        reply = client.response(client.start(method, params), timeout=args.timeout)
        elapsed = (time.perf_counter_ns() - start) / 1e6
        if "error" in reply:
            raise RuntimeError(f"{method}: {reply['error']}")
        return reply.get("result"), elapsed

    try:
        _, result["initialize_ms"] = request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": args.workspace.as_uri(),
                "capabilities": {},
            },
        )
        client.notify("initialized", {})
        for index, relative in enumerate(FILES):
            path = args.workspace / ".lake/packages/mathlib" / relative
            text, point, goal, early = fixture(path)
            uri = path.as_uri()
            doc = {"textDocument": {"uri": uri}}
            opened = time.perf_counter_ns()
            notification_offset = len(client.notifications)
            client.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": uri,
                        "languageId": "lean4",
                        "version": 1,
                        "text": text,
                    }
                },
            )
            client.until(
                lambda uri=uri, notification_offset=notification_offset: any(
                    n["method"] == "textDocument/publishDiagnostics"
                    and n["params"]["uri"] == uri
                    and n["params"]["diagnostics"]
                    for n in client.notifications[notification_offset:]
                ),
                timeout=args.timeout,
            )
            first_diagnostic_ns = time.perf_counter_ns()
            if index == 0:
                result["startup_first_diagnostic_ms"] = (
                    first_diagnostic_ns - started
                ) / 1e6
            _, wait_ms = request(
                "textDocument/waitForDiagnostics", {"uri": uri, "version": 1}
            )
            completed = time.perf_counter_ns()
            print(f"{label} {repetition}: {relative} の解析完了", flush=True)
            diagnostics = [
                n["params"]
                for n in client.notifications[notification_offset:]
                if n["method"] == "textDocument/publishDiagnostics"
                and n["params"]["uri"] == uri
            ]
            assert diagnostics, relative
            assert not [
                d for d in diagnostics[-1]["diagnostics"] if d.get("severity") == 1
            ], diagnostics[-1]
            entry = {
                "path": relative,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "bytes": path.stat().st_size,
                "lines": len(path.read_text().splitlines()),
                "direct_imports": sum(
                    line.startswith(("import ", "public import "))
                    for line in path.read_text().splitlines()
                ),
                "first_diagnostic_ms": (first_diagnostic_ns - opened) / 1e6,
                "open_complete_ms": (completed - opened) / 1e6,
                "wait_diagnostics_ms": wait_ms,
                "requests": {},
                "edits": {},
                "response_hashes": {},
            }
            for method in METHODS:
                params = (
                    doc
                    if method.endswith("/full")
                    else dict(
                        doc, position=goal if method == "$/lean/plainGoal" else point
                    )
                )
                if method == "textDocument/references":
                    params["context"] = {"includeDeclaration": True}
                response, first = request(method, params)
                assert response, (relative, method, response)
                if method == "textDocument/hover":
                    assert "twice" in response["contents"]["value"], response
                if method == "$/lean/plainGoal":
                    assert response["goals"], response
                entry["response_hashes"][method] = hashlib.sha256(
                    json.dumps(response, sort_keys=True, ensure_ascii=False).encode()
                ).hexdigest()
                entry["requests"][method] = {
                    "first_ms": first,
                    **stats([request(method, params)[1] for _ in range(args.samples)]),
                }
            version = 1
            for kind, edit_position in [("tail", goal), ("early", early)]:
                values = []
                for edit in range(args.edits):
                    version += 1
                    start = time.perf_counter_ns()
                    end = dict(
                        edit_position, character=edit_position["character"] + (edit % 2)
                    )
                    client.notify(
                        "textDocument/didChange",
                        {
                            "textDocument": {"uri": uri, "version": version},
                            "contentChanges": [
                                {
                                    "range": {"start": edit_position, "end": end},
                                    "text": " " if edit % 2 == 0 else "",
                                }
                            ],
                        },
                    )
                    request(
                        "textDocument/waitForDiagnostics",
                        {"uri": uri, "version": version},
                    )
                    values.append((time.perf_counter_ns() - start) / 1e6)
                entry["edits"][kind] = {"position": edit_position, **stats(values)}
            result["files"].append(entry)
            print(f"{label} {repetition}: {relative} の計測完了", flush=True)
        request("shutdown", None)
        client.notify("exit")
        client.process.stdin.close()
        client.process.wait(timeout=10)
    except Exception as error:
        result["failure"] = repr(error)
        raise
    finally:
        result["resources"] = monitor.finish()
        result["stderr"] = client.errors
        family = descendants(processes(), client.process.pid)
        for pid in family - {client.process.pid}:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        client.close()
        if "failure" in result:
            failure = args.output.with_name(
                f"{args.output.stem}-{label}-{repetition}-failure.json"
            )
            failure.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    assert not result["resources"]["sampling_error"], result["resources"]
    result["accepted"] = (
        result["resources"]["outside_cpu_cores"] <= args.max_outside_cores
    )
    return result


def main(session_runner=session):
    parser = argparse.ArgumentParser(
        description="Mathlib の実ファイルを用いた LSP 比較計測"
    )
    parser.add_argument("--workspace", type=Path, default=ROOT / "target/mathlib")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "target/mathlib-results.json"
    )
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--toolchain", type=Path)
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--edits", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--max-outside-cores", type=float, default=0.75)
    parser.add_argument("--wait-idle", action="store_true")
    parser.add_argument("--retry-delay", type=float, default=30)
    parser.add_argument("--max-attempts", type=int, default=200)
    parser.add_argument(
        "--server",
        choices=["upstream", "leansail", "before", "native"],
        action="append",
    )
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--perf", action="store_true")
    parser.add_argument("--host", type=Path, default=ROOT / "target/release/leansail")
    parser.add_argument("--backend", type=Path)
    parser.add_argument("--before-backend", type=Path)
    args = parser.parse_args()
    if session_runner != session and (args.profile or args.perf):
        parser.error("並列シナリオは通常計測として実行してください")
    args.workspace = args.workspace.resolve()
    args.output = args.output.resolve()
    args.host = args.host.resolve()
    if args.before_backend:
        args.before_backend = args.before_backend.resolve()
    if args.server and "before" in args.server and not args.before_backend:
        parser.error("before の計測には --before-backend が必要です")
    if args.retry_delay <= 0 or args.max_attempts < 1:
        parser.error("再試行の間隔と上限は正数にしてください")
    if args.toolchain:
        args.toolchain = args.toolchain.resolve()
        os.environ["PATH"] = (
            str(args.toolchain / "bin") + os.pathsep + os.environ["PATH"]
        )
        os.environ["LEAN_SYSROOT"] = str(args.toolchain)
    if args.samples < 1 or args.repetitions < 1 or args.edits < 2 or args.edits % 2:
        parser.error("標本と反復は正数、編集回数は 2 以上の偶数にしてください")
    if platform.system() != "Linux":
        parser.error("RSS と負荷の計測には Linux の /proc が必要です")
    lean_hash = run(["lean", "--githash"], ROOT, capture_output=True).stdout.strip()
    if lean_hash != "eb6cafb7290e8bd5f2937522d8d804f58f02bafc":
        parser.error(
            "公式 Lean 4.35.0 の git hash が必要です。--toolchain に公式 toolchain のルートを指定してください"
        )
    if args.prepare or args.prepare_only:
        prepare(args.workspace)
    if args.prepare_only:
        return
    args.backend = args.backend.resolve() if args.backend else build_backend()
    os.chdir(args.workspace)
    env_json = run(
        [
            "lake",
            "env",
            "python3",
            "-c",
            "import os,json; print(json.dumps(dict(os.environ)))",
        ],
        args.workspace,
        capture_output=True,
    ).stdout
    os.environ.update(json.loads(env_json))
    mathlib = args.workspace / ".lake/packages/mathlib"
    actual_revision = run(
        ["git", "rev-parse", "HEAD"], mathlib, capture_output=True
    ).stdout.strip()
    assert actual_revision == REVISION, actual_revision
    assert (
        "4.35.0"
        in run(["lean", "--version"], args.workspace, capture_output=True).stdout
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    commands = {
        "upstream": [shutil.which("lean"), "--server"],
        "leansail": [
            str(args.host),
            "server",
            "run-server",
            "--backend",
            str(args.backend),
        ],
        "native": [str(args.backend), "--server"],
    }
    if args.before_backend:
        commands["before"] = [
            str(args.host),
            "server",
            "run-server",
            "--backend",
            str(args.before_backend),
        ]
    output = {
        "mathlib_revision": REVISION,
        "samples": args.samples,
        "repetitions": args.repetitions,
        "edits": args.edits,
        "cpu_count": os.cpu_count(),
        "machine": {"hostname": platform.node(), "platform": platform.platform()},
        "scenario": session_runner.__module__ + "." + session_runner.__name__,
        "loadavg": os.getloadavg(),
        "profile": args.profile,
        "perf": args.perf,
        "host_sha256": hashlib.sha256(args.host.read_bytes()).hexdigest(),
        "backend_sha256": hashlib.sha256(args.backend.read_bytes()).hexdigest(),
        "before_backend_sha256": hashlib.sha256(
            args.before_backend.read_bytes()
        ).hexdigest()
        if args.before_backend
        else None,
        "lean_git_hash": lean_hash,
        "max_outside_cores": args.max_outside_cores,
        "sessions": [],
        "preflight_skips": [],
        "interrupted_sessions": [],
    }
    if args.resume and args.output.exists():
        previous = json.loads(args.output.read_text())
        for key in [
            "mathlib_revision",
            "samples",
            "repetitions",
            "edits",
            "profile",
            "perf",
            "host_sha256",
            "backend_sha256",
            "max_outside_cores",
            "machine",
            "cpu_count",
            "scenario",
            "before_backend_sha256",
        ]:
            if previous[key] != output[key]:
                raise ValueError(f"再開時の設定が一致しません: {key}")
        output = previous
    for repetition in range(args.repetitions):
        labels = args.server or ["upstream", "leansail"]
        for label in labels if repetition % 2 == 0 else reversed(labels):
            if any(
                s["server"] == label and s["repetition"] == repetition and s["accepted"]
                for s in output["sessions"]
            ):
                continue
            command = commands[label]
            if args.perf:
                command = [
                    "perf",
                    "record",
                    "-e",
                    "cpu-clock:u",
                    "-F",
                    "99",
                    "--call-graph",
                    "dwarf,8192",
                    "-o",
                    str(args.output.parent / f"{label}-{repetition}.perf.data"),
                    "--",
                    *command,
                ]
            for attempt in range(args.max_attempts):
                before = processes()
                start = time.monotonic()
                time.sleep(2)
                after = processes()
                idle_cores = outside_cpu(
                    before, after, {os.getpid()}, time.monotonic() - start
                )
                if idle_cores > args.max_outside_cores:
                    output["preflight_skips"].append(
                        {
                            "server": label,
                            "repetition": repetition,
                            "outside_cpu_cores": idle_cores,
                        }
                    )
                    measured = None
                else:
                    try:
                        measured = session_runner(args, label, command, repetition)
                    except KeyboardInterrupt:
                        output["interrupted_sessions"].append(
                            {
                                "server": label,
                                "repetition": repetition,
                                "reason": "操作による中断",
                            }
                        )
                        args.output.write_text(
                            json.dumps(output, ensure_ascii=False, indent=2) + "\n"
                        )
                        raise
                    output["sessions"].append(measured)
                args.output.write_text(
                    json.dumps(output, ensure_ascii=False, indent=2) + "\n"
                )
                if measured and measured["accepted"]:
                    print(f"{label} {repetition}: 採用", flush=True)
                    break
                if not args.wait_idle or attempt + 1 == args.max_attempts:
                    raise RuntimeError(
                        "外部負荷が上限を超えました。--resume で再開できます"
                    )
                print(
                    f"{label} {repetition}: 負荷のため {args.retry_delay} 秒後に再試行",
                    flush=True,
                )
                time.sleep(args.retry_delay)


if __name__ == "__main__":
    main()

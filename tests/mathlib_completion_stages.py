import json
import os
import sys
import time

import mathlib
import mathlib_parallel
from mathlib import main


def stages_session(
    runner, client_module, client_name, args, label, command, repetition
):
    trace_dir = args.output.parent / (args.output.stem + "-traces")
    trace_dir.mkdir(parents=True, exist_ok=True)
    prefix = trace_dir / f"{label}-{repetition}-{time.monotonic_ns()}"
    original = getattr(client_module, client_name)
    client_events = []

    class TracedClient(original):
        def __init__(self, *args, **kwargs):
            self.completion_requests = {}
            super().__init__(*args, **kwargs)

        def start(self, method, params=None):
            ns = time.monotonic_ns()
            request_id = super().start(method, params)
            if method in ["textDocument/completion", "completionItem/resolve"]:
                self.completion_requests[request_id] = method
                client_events.append(
                    {
                        "event": "client_start",
                        "method": method,
                        "id": request_id,
                        "ns": ns,
                        "pid": os.getpid(),
                    }
                )
            return request_id

        def receive(self, timeout):
            result = super().receive(timeout)
            request_id = result.get("id")
            if "method" not in result and request_id in self.completion_requests:
                client_events.append(
                    {
                        "event": "client_received",
                        "method": self.completion_requests[request_id],
                        "id": request_id,
                        "ns": time.monotonic_ns(),
                        "pid": os.getpid(),
                    }
                )
            return result

    setattr(client_module, client_name, TracedClient)
    environment = {
        name: os.environ.get(name)
        for name in ["LEANSAIL_TRACE", "LEANSAIL_COMPLETION_PROFILE"]
    }
    os.environ["LEANSAIL_TRACE"] = str(prefix)
    os.environ["LEANSAIL_COMPLETION_PROFILE"] = "1"
    try:
        result = runner(args, label, command, repetition)
        events = client_events + [
            json.loads(line)
            for path in prefix.parent.glob(prefix.name + ".*.jsonl")
            for line in path.read_text().splitlines()
        ]
        result["events"] = sorted(events, key=lambda event: event["ns"])
        result["completion_stages"] = True
        return result
    finally:
        setattr(client_module, client_name, original)
        for name, value in environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def normal_stages_session(*args):
    return stages_session(mathlib.session, mathlib, "Client", *args)


def parallel_stages_session(*args):
    return stages_session(
        mathlib_parallel.parallel_session, mathlib_parallel, "TimedClient", *args
    )


if __name__ == "__main__":
    parallel = "--parallel" in sys.argv
    if parallel:
        sys.argv.remove("--parallel")
    main(parallel_stages_session if parallel else normal_stages_session)

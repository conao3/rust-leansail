import json
import os
import queue
import signal
import subprocess
import threading
import time


class Client:
    def __init__(self, command, env=None):
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            env=env,
        )
        self.messages = queue.Queue()
        self.notifications = []
        self.server_requests = []
        self.responses = {}
        self.transcript = []
        self.errors = []
        self.next_id = 0
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.stderr_reader = threading.Thread(target=self.read_errors, daemon=True)
        self.reader.start()
        self.stderr_reader.start()

    def read(self):
        try:
            while True:
                headers = {}
                while (line := self.process.stdout.readline()) not in (b"\r\n", b"\n"):
                    if not line:
                        raise EOFError("サーバーの標準出力が終了しました")
                    key, value = line.decode().split(":", 1)
                    headers[key.lower()] = value.strip()
                size = int(headers["content-length"])
                body = self.process.stdout.read(size)
                if len(body) != size:
                    raise EOFError("LSP frame が途中で終了しました")
                self.messages.put(json.loads(body))
        except (EOFError, ValueError, KeyError, OSError) as error:
            self.messages.put(error)

    def read_errors(self):
        for line in self.process.stderr:
            self.errors.append(line.decode(errors="replace"))

    def send(self, message):
        message = dict(jsonrpc="2.0", **message)
        self.transcript.append({"send": message})
        data = json.dumps(message, ensure_ascii=False).encode()
        self.process.stdin.write(f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
        self.process.stdin.flush()

    def notify(self, method, params=None):
        self.send(
            {"method": method, **({"params": params} if params is not None else {})}
        )

    def start(self, method, params=None):
        self.next_id += 1
        self.send(
            {"id": self.next_id, "method": method}
            | ({"params": params} if params is not None else {})
        )
        return self.next_id

    def receive(self, timeout):
        message = self.messages.get(timeout=timeout)
        if isinstance(message, Exception):
            raise message
        self.transcript.append({"receive": message})
        if "method" in message:
            if "id" in message:
                self.server_requests.append(message)
                self.send({"id": message["id"], "result": None})
            else:
                self.notifications.append(message)
        else:
            assert message["id"] not in self.responses, message
            self.responses[message["id"]] = message
        return message

    def until(self, predicate, timeout=30):
        deadline = time.monotonic() + timeout
        while not predicate():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"受信待ちが期限を超えました: {self.errors}")
            self.receive(remaining)

    def response(self, request_id, timeout=30):
        self.until(lambda: request_id in self.responses, timeout)
        return self.responses[request_id]

    def request(self, method, params=None):
        response = self.response(self.start(method, params))
        assert "error" not in response, (method, response)
        return response.get("result")

    def drain(self, seconds=0.2):
        deadline = time.monotonic() + seconds
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                self.receive(remaining)
            except queue.Empty:
                break

    def close(self):
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.process.wait(timeout=10)
        self.reader.join(timeout=2)
        self.stderr_reader.join(timeout=2)
        self.process.stdin.close()
        self.process.stdout.close()
        self.process.stderr.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

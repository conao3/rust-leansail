import json
import os
import selectors
import subprocess
import sys
from pathlib import Path


def main():
    config = json.loads(Path(os.environ["LEANSAIL_EDITOR_CONFIG"]).read_text())
    with (
        Path(config["transcript"]).open("w", buffering=1) as log,
        Path(config["stderr"]).open("w") as errors,
    ):
        process = subprocess.Popen(
            config["server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=errors,
        )
        selector = selectors.DefaultSelector()
        selector.register(sys.stdin.buffer, selectors.EVENT_READ, "send")
        selector.register(process.stdout, selectors.EVENT_READ, "receive")
        buffers = {"send": b"", "receive": b""}
        try:
            while selector.get_map():
                for key, _ in selector.select(timeout=30):
                    direction = key.data
                    data = os.read(key.fd, 65536)
                    if not data:
                        selector.unregister(key.fileobj)
                        if direction == "send":
                            process.stdin.close()
                        else:
                            selector.close()
                            break
                        continue
                    buffers[direction] += data
                    while b"\r\n\r\n" in buffers[direction]:
                        header, body = buffers[direction].split(b"\r\n\r\n", 1)
                        headers = dict(
                            line.lower().split(b":", 1)
                            for line in header.split(b"\r\n")
                        )
                        size = int(headers[b"content-length"])
                        if len(body) < size:
                            break
                        log.write(
                            json.dumps({direction: json.loads(body[:size])}) + "\n"
                        )
                        buffers[direction] = body[size:]
                    output = process.stdin if direction == "send" else sys.stdout.buffer
                    output.write(data)
                    output.flush()
                if process.poll() is not None:
                    break
            status = process.wait(timeout=10)
            log.write(json.dumps({"exit_code": status}) + "\n")
            return status
        finally:
            selector.close()
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(main())

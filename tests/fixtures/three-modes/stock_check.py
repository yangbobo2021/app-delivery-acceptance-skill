"""Existing black-box test for the stock service, using only standard Python."""
import contextlib
import http.client
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time


@contextlib.contextmanager
def server(binary, directory):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen([binary, "--port", str(port), "--state-dir", directory])
    try:
        for _ in range(100):
            try:
                request(port, "GET", "/stock")
                break
            except OSError:
                if process.poll() is not None:
                    raise
                time.sleep(0.02)
        else:
            raise RuntimeError("stock server did not start")
        yield port
    finally:
        process.terminate()
        process.wait(timeout=5)


def request(port, method, route, data=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request(method, route, json.dumps(data) if data else None,
                           {"Content-Type": "application/json"})
        reply = connection.getresponse()
        return reply.status, json.loads(reply.read())
    finally:
        connection.close()


def reserve(port, quantity):
    return request(port, "POST", "/reserve", {"request_id": "same-order", "quantity": quantity})


# @case-id: TC-same-process
def same_process(binary, directory):
    with server(binary, directory) as port:
        status, original = reserve(port, 2)
        assert status == 201 and original["available"] == 8
        assert reserve(port, 2) == (200, original)
        assert reserve(port, 1)[0] == 409
        assert request(port, "GET", "/stock")[1]["available"] == 8


def run(check):
    with tempfile.TemporaryDirectory() as directory:
        check(str(Path("build/stock").resolve()), directory)
    print("verified", check.__name__)


if __name__ == "__main__":
    run(same_process)

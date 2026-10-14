import json
import os
import subprocess
import sys
import threading
from pathlib import Path


def bundled_command(script_name):
    """Shell command that starts one of the project MCP servers."""
    script = Path(__file__).resolve().parents[2] / script_name
    return subprocess.list2cmdline([sys.executable, str(script)])


def call_tool(command, name, arguments, extra_env=None, failure_code="mcp_unavailable"):
    """Call one tool on an MCP server started from a shell command."""
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        shell=True,
    )
    timer = threading.Timer(25, process.kill)
    timer.start()
    try:
        _exchange(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "smart-shift-manager", "version": "1.0.0"},
                },
            },
        )
        _write(
            process,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        response = _exchange(
            process,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        )
    except Exception:
        return {"ok": False, "error_code": failure_code}
    finally:
        timer.cancel()
        if process.poll() is None:
            process.kill()
    content = ((response.get("result") or {}).get("content") or [{}])[0].get("text") or ""
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {"ok": False, "error_code": failure_code}


def run_stdio_server(handle):
    """Read MCP messages from stdin until the client closes the pipe."""
    while True:
        message = read_message(sys.stdin.buffer)
        if message is None:
            return
        response = handle(message)
        if response is not None:
            write_message(sys.stdout.buffer, response)


def read_message(stream):
    headers = {}
    while True:
        line = stream.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        decoded = line.decode("ascii", errors="replace")
        if ":" not in decoded:
            continue
        name, value = decoded.split(":", 1)
        headers[name.strip().lower()] = value.strip()
    length = int(headers.get("content-length") or "0")
    if length <= 0:
        return None
    return json.loads(stream.read(length).decode("utf-8"))


def write_message(stream, payload):
    raw = json.dumps(payload).encode("utf-8")
    stream.write("Content-Length: {0}\r\n\r\n".format(len(raw)).encode("ascii") + raw)
    stream.flush()


def _exchange(process, payload):
    _write(process, payload)
    response = read_message(process.stdout)
    if response is None:
        raise RuntimeError("MCP server closed the connection.")
    return response


def _write(process, payload):
    write_message(process.stdin, payload)

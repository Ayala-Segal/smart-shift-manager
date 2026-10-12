import json
import os
import subprocess


def call_tool(command, name, arguments, extra_env=None):
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
    finally:
        process.kill()
    content = ((response.get("result") or {}).get("content") or [{}])[0].get("text") or ""
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {"ok": False, "error_code": "gmail_unavailable"}


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

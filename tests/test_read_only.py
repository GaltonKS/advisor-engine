"""Read, never write: no code outside the API may send a write request anywhere.

Connectors to QuickBooks and sector systems read only. This parses every module of the
package outside `api/` (the shared layer, the engine, the assistant and any ingestion or
connector package added later, without editing a list) and fails on:

- a call to `.post`, `.put`, `.patch` or `.delete` on an httpx client or the httpx
  module. Types aren't known from the AST, so a call counts when its module imports
  httpx, or when the receiver is named like one (`httpx`, `client`, `self.http`,
  `session`, ...). That leaves the disk cache's own `put` alone;
- `.request(...)`, `.stream(...)` or `.build_request(...)` unless the method is a literal
  "GET" or "HEAD" (a method held in a variable could be anything);
- importing an HTTP library other than httpx (`requests`, `urllib.request`,
  `http.client`, `aiohttp`, `urllib3`), which this check would not see.

`api/` is out of scope: its `@router.post` decorators are our own inbound routes, and its
outbound calls go to our own Supabase (auth admin, Storage), never to a client's system.
"""

import ast
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1] / "src" / "advisor_engine"
EXCLUDED = ("api",)

WRITE_METHODS = {"post", "put", "patch", "delete"}
METHOD_ARG_CALLS = {"request", "stream", "build_request"}
READ_VERBS = {"GET", "HEAD"}
OTHER_HTTP = {"requests", "urllib.request", "http.client", "aiohttp", "urllib3"}


def scanned_files() -> list[Path]:
    return sorted(p for p in PKG.rglob("*.py")
                  if p.relative_to(PKG).parts[0] not in EXCLUDED)


def _receiver_name(func: ast.Attribute) -> str:
    v = func.value
    if isinstance(v, ast.Attribute):
        return v.attr
    if isinstance(v, ast.Name):
        return v.id
    if isinstance(v, ast.Call):  # httpx.Client().post(...)
        return _receiver_name(v.func) if isinstance(v.func, ast.Attribute) else ""
    return ""


def _looks_like_http(name: str) -> bool:
    n = name.lower()
    return any(w in n for w in ("httpx", "client", "http", "session"))


def _imports_httpx(tree: ast.AST) -> bool:
    return any(
        (isinstance(n, ast.Import) and any(a.name.split(".")[0] == "httpx" for a in n.names))
        or (isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] == "httpx")
        for n in ast.walk(tree)
    )


def _method_arg(call: ast.Call) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == "method":
            return kw.value
    return call.args[0] if call.args else None


def write_calls(source: str) -> list[str]:
    """Every write-capable HTTP call or non-httpx HTTP import in `source`, as 'line: what'."""
    out = []
    tree = ast.parse(source)
    http_module = _imports_httpx(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            name = node.func.attr
            if name in WRITE_METHODS and (http_module or _looks_like_http(_receiver_name(node.func))):
                out.append(f"{node.lineno}: .{name}()")
            elif name in METHOD_ARG_CALLS:
                m = _method_arg(node)
                if not (isinstance(m, ast.Constant) and isinstance(m.value, str)
                        and m.value.upper() in READ_VERBS):
                    out.append(f"{node.lineno}: .{name}() with a method that isn't GET")
        elif isinstance(node, ast.Import):
            out += [f"{node.lineno}: import {a.name}" for a in node.names
                    if any(a.name == h or a.name.startswith(h + ".") for h in OTHER_HTTP)]
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            mods = {node.module} | {f"{node.module}.{a.name}" for a in node.names}
            if any(m == h or m.startswith(h + ".") for m in mods for h in OTHER_HTTP):
                out.append(f"{node.lineno}: from {node.module} import ...")
    return out


def test_scope_covers_the_shared_layer_and_vendor_adapters():
    rel = {p.relative_to(PKG).as_posix() for p in scanned_files()}
    assert {"analysis.py", "config.py", "data/fmp.py", "data/twelvedata.py"} <= rel
    assert not any(r.startswith("api/") for r in rel)


@pytest.mark.parametrize("path", scanned_files(), ids=lambda p: p.relative_to(PKG).as_posix())
def test_no_module_outside_the_api_writes_over_http(path):
    assert write_calls(path.read_text()) == [], path


# Guards the guard: each of these must be caught, and reads must not be.
@pytest.mark.parametrize("src", [
    "import httpx\nhttpx.post('https://quickbooks.api/x', json={})",
    "self.http.put(url)",
    "client.patch(url)",
    "client.delete(url)",
    "client.request('POST', url)",
    "client.request(method, url)",
    "client.request(url=u, method='DELETE')",
    "client.stream('PUT', url)",
    "client.build_request('POST', url)",
    "import httpx\n\ndef push(c):\n    c.post(url)",
    "def push(client):\n    client.post(url)",
    "httpx.Client().post(url)",
    "import requests",
    "from urllib.request import urlopen",
    "import http.client",
])
def test_write_calls_are_caught(src):
    assert write_calls(src)


@pytest.mark.parametrize("src", [
    "import httpx\nhttpx.get(url)",
    "client.get(url, params={'symbol': s})",
    "client.request('GET', url)",
    "client.request('get', url)",
    "client.stream('GET', url)",
    "client.request(method='HEAD', url=u)",
    "self.put(source, symbol, closes)",  # the disk cache, no httpx in the module
    "cache.delete(key)",
])
def test_reads_pass(src):
    assert write_calls(src) == []

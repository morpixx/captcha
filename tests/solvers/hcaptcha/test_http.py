from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import msgspec
import pytest

from captcha.adapters.hcaptcha_js import runner as js_runner
from captcha.adapters.hcaptcha_js.runner import JsBundleRunner
from captcha.domain import CaptchaType, SolveMode, Task
from captcha.domain.errors import ConfigError, JSRuntimeError, SensorError, UnsolvableError
from captcha.domain.task import new_task_id
from captcha.solvers import SolverContext
from captcha.solvers.hcaptcha import wire
from captcha.solvers.hcaptcha.http import HCaptchaHTTPSolver, NeedsBrowserError

FIXTURES = Path(__file__).with_name("fixtures")
SITEKEY = "10000000-ffff-ffff-ffff-000000000001"
PAGE_URL = "https://example.com/login"
TOKEN = "P1_eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.c2ln"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def encrypted_body() -> bytes:
    return (FIXTURES / "getcaptcha_pass.enc").read_bytes()


class FakeResponse:
    """Duck-typed stand-in for captcha.adapters.transport.Response."""

    def __init__(self, body: Any, *, status: int = 200, content_type: str = "application/json") -> None:
        self.status = status
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.headers = [("content-type", content_type)] if content_type else []
        self.elapsed_ms = 0.0
        self.final_url = ""

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.body)

    def header(self, name: str) -> str | None:
        for key, value in self.headers:
            if key.lower() == name.lower():
                return value
        return None


class FakeHTTP:
    """Records every outbound request and replays queued responses."""

    def __init__(self, *responses: FakeResponse) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if not self.responses:
            raise AssertionError(f"unexpected POST {url}")
        return self.responses.pop(0)


class FakeBundle:
    """Stands in for the Node hsw/hsj worker."""

    def __init__(
        self,
        *,
        n_token: str = "n-token-abc",
        sealed: bytes = b"sealed-nonce-cipher-tag",
        plaintext: bytes | None = None,
    ) -> None:
        self.n_token = n_token
        self.sealed = sealed
        self.plaintext = plaintext if plaintext is not None else msgspec.msgpack.encode(fixture("getcaptcha_pass.json"))
        self.solve_calls: list[dict[str, Any]] = []
        self.encrypt_calls: list[dict[str, Any]] = []
        self.decrypt_calls: list[dict[str, Any]] = []

    async def solve(self, *, req: str, host: str, href: str, config: dict[str, Any], vm_data: str | None = None) -> str:
        self.solve_calls.append({"req": req, "host": host, "href": href, "config": config, "vm_data": vm_data})
        return self.n_token

    async def encrypt(self, req: str, plaintext: bytes, config: dict[str, Any]) -> bytes:
        self.encrypt_calls.append({"req": req, "plaintext": plaintext, "config": config})
        return self.sealed

    async def decrypt(self, req: str, blob: bytes, config: dict[str, Any]) -> bytes:
        self.decrypt_calls.append({"req": req, "blob": blob, "config": config})
        return self.plaintext


def make_task(**overrides: Any) -> Task:
    payload = {"sitekey": SITEKEY, "pageurl": PAGE_URL}
    payload.update(overrides.pop("payload", {}))
    fields: dict[str, Any] = {"id": new_task_id(), "type": CaptchaType.HCAPTCHA, "payload": payload}
    fields.update(overrides)
    return Task(**fields)


async def solve(http: FakeHTTP, bundle: FakeBundle | None, task: Task | None = None):
    extra = {"hcaptcha_js": bundle} if bundle is not None else {}
    ctx = SolverContext(http=http, extra=extra)
    return await HCaptchaHTTPSolver().solve_http(task or make_task(), ctx)


# --- checksiteconfig ---------------------------------------------------------


async def test_checksiteconfig_request_shape():
    http = FakeHTTP(FakeResponse(fixture("checksiteconfig_plain.json")), FakeResponse(fixture("getcaptcha_pass.json")))
    bundle = FakeBundle()

    await solve(http, bundle)

    call = http.calls[0]
    assert call["url"] == "https://api.hcaptcha.com/checksiteconfig"
    assert call["params"] == {
        "v": wire.DEFAULT_VERSION,
        "host": "example.com",
        "sitekey": SITEKEY,
        "sc": "1",
        "swa": "1",
        "spst": "1",
    }
    assert call["data"] == b""
    assert call["headers"]["Content-Type"] == "text/plain"
    assert call["headers"]["Referer"] == "https://newassets.hcaptcha.com/"
    assert call["headers"]["User-Agent"] == wire.DEFAULT_UA


async def test_checksiteconfig_forwards_enterprise_rqdata():
    http = FakeHTTP(FakeResponse(fixture("checksiteconfig_plain.json")), FakeResponse(fixture("getcaptcha_pass.json")))
    task = make_task(payload={"rqdata": "ent-rqdata-token"})

    await solve(http, FakeBundle(), task)

    assert http.calls[0]["params"]["rqdata"] == "ent-rqdata-token"
    assert http.calls[1]["data"]["rqdata"] == "ent-rqdata-token"


async def test_checksiteconfig_http_error_is_sensor_error():
    http = FakeHTTP(FakeResponse(b"", status=503, content_type="text/html"))
    with pytest.raises(SensorError) as exc:
        await solve(http, FakeBundle())
    assert "503" in exc.value.message


async def test_checksiteconfig_without_challenge_is_unsolvable():
    http = FakeHTTP(FakeResponse({"pass": False}))
    with pytest.raises(UnsolvableError):
        await solve(http, FakeBundle())


# --- n-token -----------------------------------------------------------------


async def test_bundle_gets_challenge_jwt_and_page():
    checksiteconfig = fixture("checksiteconfig_plain.json")
    http = FakeHTTP(FakeResponse(checksiteconfig), FakeResponse(fixture("getcaptcha_pass.json")))
    bundle = FakeBundle()
    task = make_task(user_agent="Mozilla/5.0 (X11; Linux x86_64) Test/1.0")

    await solve(http, bundle, task)

    call = bundle.solve_calls[0]
    assert call["req"] == checksiteconfig["c"]["req"]
    assert call["host"] == "example.com"
    assert call["href"] == PAGE_URL
    assert call["vm_data"] is None
    assert call["config"] == {"userAgent": "Mozilla/5.0 (X11; Linux x86_64) Test/1.0"}
    assert http.calls[0]["headers"]["User-Agent"] == "Mozilla/5.0 (X11; Linux x86_64) Test/1.0"


async def test_empty_n_token_is_sensor_error():
    http = FakeHTTP(FakeResponse(fixture("checksiteconfig_plain.json")))
    with pytest.raises(SensorError):
        await solve(http, FakeBundle(n_token=""))


# --- getcaptcha: plaintext ---------------------------------------------------


async def test_plaintext_getcaptcha_form():
    checksiteconfig = fixture("checksiteconfig_plain.json")
    http = FakeHTTP(FakeResponse(checksiteconfig), FakeResponse(fixture("getcaptcha_pass.json")))

    await solve(http, FakeBundle())

    call = http.calls[1]
    assert call["url"] == f"https://api.hcaptcha.com/getcaptcha/{SITEKEY}"
    assert call["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert call["headers"]["Origin"] == "https://newassets.hcaptcha.com"
    form = call["data"]
    assert form["v"] == wire.DEFAULT_VERSION
    assert form["sitekey"] == SITEKEY
    assert form["host"] == "example.com"
    assert form["hl"] == "en"
    assert form["n"] == "n-token-abc"
    assert form["pst"] == "false"
    assert "e" not in form
    assert json.loads(form["c"]) == checksiteconfig["c"]
    motion = json.loads(form["motionData"])
    assert set(motion) == {"st", "dct", "mm", "md", "mu", "v"}


# --- getcaptcha: encrypted ---------------------------------------------------


async def test_encrypted_getcaptcha_body_is_msgpack_ext():
    checksiteconfig = fixture("checksiteconfig_hsw.json")
    http = FakeHTTP(
        FakeResponse(checksiteconfig),
        FakeResponse(encrypted_body(), content_type="application/octet-stream"),
    )
    bundle = FakeBundle()

    result = await solve(http, bundle)

    call = http.calls[1]
    assert call["headers"]["Content-Type"] == "application/octet-stream"
    decoded = msgspec.msgpack.decode(call["data"])
    assert isinstance(decoded, list) and len(decoded) == 2
    assert json.loads(decoded[0]) == checksiteconfig["c"]
    assert isinstance(decoded[1], msgspec.msgpack.Ext)
    assert decoded[1].code == 18
    assert decoded[1].data == b"sealed-nonce-cipher-tag"

    encrypted_plaintext = msgspec.msgpack.decode(bundle.encrypt_calls[0]["plaintext"])
    assert encrypted_plaintext["n"] == "n-token-abc"
    assert encrypted_plaintext["sitekey"] == SITEKEY

    assert bundle.decrypt_calls[0]["blob"] == encrypted_body()
    assert result.token == fixture("getcaptcha_pass.json")["generated_pass_UUID"]


# --- pass token --------------------------------------------------------------


async def test_json_pass_returns_p1_token_and_resp_key():
    http = FakeHTTP(FakeResponse(fixture("checksiteconfig_plain.json")), FakeResponse(fixture("getcaptcha_pass.json")))

    result = await solve(http, FakeBundle())

    assert result.token == fixture("getcaptcha_pass.json")["generated_pass_UUID"]
    assert result.token.startswith("P1_")
    assert result.extra is not None
    assert result.extra["respKey"] == fixture("getcaptcha_pass.json")["key"]
    assert result.extra["userAgent"] == wire.DEFAULT_UA
    assert result.solve_mode is SolveMode.HTTP
    assert result.latency_ms >= 0.0


async def test_encrypted_pass_returns_p1_token():
    http = FakeHTTP(
        FakeResponse(fixture("checksiteconfig_hsw.json")),
        FakeResponse(encrypted_body(), content_type="application/octet-stream"),
    )
    bundle = FakeBundle(plaintext=msgspec.msgpack.encode({"generated_pass_UUID": TOKEN, "pass": True, "key": "k-9"}))

    result = await solve(http, bundle)

    assert result.token == TOKEN
    assert result.extra is not None and result.extra["respKey"] == "k-9"


# --- challenge / rejection ---------------------------------------------------


async def test_image_challenge_raises_needs_browser():
    http = FakeHTTP(
        FakeResponse(fixture("checksiteconfig_plain.json")), FakeResponse(fixture("getcaptcha_challenge.json"))
    )

    with pytest.raises(NeedsBrowserError) as exc:
        await solve(http, FakeBundle())

    assert exc.value.code == "ERR_NEEDS_BROWSER"
    assert exc.value.context["challenge"] == "image_label_binary"
    assert exc.value.context["sitekey"] == SITEKEY
    assert exc.value.context["host"] == "example.com"


async def test_encrypted_challenge_also_escalates():
    http = FakeHTTP(
        FakeResponse(fixture("checksiteconfig_hsw.json")),
        FakeResponse(encrypted_body(), content_type="application/octet-stream"),
    )
    bundle = FakeBundle(plaintext=msgspec.msgpack.encode(fixture("getcaptcha_challenge.json")))

    with pytest.raises(NeedsBrowserError):
        await solve(http, bundle)


async def test_error_codes_raise_sensor_error():
    http = FakeHTTP(FakeResponse(fixture("checksiteconfig_plain.json")), FakeResponse(fixture("getcaptcha_error.json")))

    with pytest.raises(SensorError) as exc:
        await solve(http, FakeBundle())

    assert exc.value.context["error_codes"] == ["invalid-n"]
    assert exc.value.context["success"] is False


async def test_non_p1_token_without_pass_is_sensor_error():
    body = {"generated_pass_UUID": "not-a-pass", "pass": False}
    http = FakeHTTP(FakeResponse(fixture("checksiteconfig_plain.json")), FakeResponse(body))

    with pytest.raises(SensorError):
        await solve(http, FakeBundle())


# --- payload / config guards -------------------------------------------------


async def test_missing_sitekey_is_unsolvable():
    task = make_task(payload={"sitekey": None})
    with pytest.raises(UnsolvableError):
        await solve(FakeHTTP(), FakeBundle(), task)


async def test_missing_host_is_unsolvable():
    task = make_task(payload={"pageurl": None})
    with pytest.raises(UnsolvableError):
        await solve(FakeHTTP(), FakeBundle(), task)


async def test_missing_bundle_runner_is_config_error():
    http = FakeHTTP()
    with pytest.raises(ConfigError):
        await solve(http, None)


async def test_missing_http_client_is_config_error():
    ctx = SolverContext(http=None, extra={"hcaptcha_js": FakeBundle()})
    with pytest.raises(ConfigError):
        await HCaptchaHTTPSolver().solve_http(make_task(), ctx)


# --- adapter wiring ----------------------------------------------------------


def test_runner_rejects_missing_node_binary(monkeypatch):
    monkeypatch.setattr(js_runner, "_NODE_CANDIDATES", ())
    monkeypatch.setattr(js_runner.shutil, "which", lambda _name: None)
    with pytest.raises(ConfigError):
        JsBundleRunner(node_binary="node")


def test_runner_rejects_missing_server():
    node = shutil.which("node")
    binary = node or "node"
    with pytest.raises(ConfigError):
        JsBundleRunner(node_binary=binary, server_path=FIXTURES / "does-not-exist.js")


NODE_STUB = r"""
const readline = require("node:readline");
const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on("line", (line) => {
  const request = JSON.parse(line);
  if (request.mode === "solve") {
    const out = Buffer.from("n-token-from-node", "latin1").toString("base64");
    process.stdout.write(JSON.stringify({ status: "ok", kind: "string", data: out, len: 17 }) + "\n");
  } else if (request.mode === "encrypt") {
    const raw = Buffer.from(request.data, "base64");
    const flipped = Buffer.from(raw.toString("latin1").split("").reverse().join(""), "latin1");
    process.stdout.write(JSON.stringify({ status: "ok", kind: "bytes", data: flipped.toString("base64") }) + "\n");
  } else {
    process.stdout.write(JSON.stringify({ status: "error", error: "mode not supported" }) + "\n");
  }
});
"""


async def test_runner_round_trip_over_stdio(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    stub = tmp_path / "stub_server.js"
    stub.write_text(NODE_STUB, encoding="utf-8")
    runner = JsBundleRunner(node_binary=node, server_path=stub)
    try:
        token = await runner.solve(req="x.y.z", host="example.com", href=PAGE_URL, config={})
        assert token == "n-token-from-node"
        assert await runner.encrypt("x.y.z", b"abc", {}) == b"cba"
        with pytest.raises(JSRuntimeError):
            await runner.decrypt("x.y.z", b"abc", {})
    finally:
        await runner.aclose()


def test_node_worker_manifest():
    adapter = FIXTURES.parents[3] / "src" / "captcha" / "adapters" / "hcaptcha_js"
    manifest = json.loads((adapter / "package.json").read_text(encoding="utf-8"))
    assert {"jsdom", "canvas"} <= set(manifest["dependencies"])
    assert (adapter / "server.js").is_file()
    assert (adapter / "bundle.js").is_file()
    assert (adapter / "realm.js").is_file()

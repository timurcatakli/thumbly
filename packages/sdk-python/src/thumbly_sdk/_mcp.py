"""MCP tool calls over Streamable HTTP, Thumbly's one public interface.

Thumbly serves MCP statelessly with JSON responses, so a tool call is a single JSON-RPC POST to
``/mcp`` with the bearer token; no session, no event stream. Bodies are bounded both ways.
"""

import base64
import itertools
import json
import time
from collections.abc import Callable
from typing import Any

import httpx

from thumbly_sdk.errors import RateLimited, ResponseTooLarge, ThumblyError

MAX_REQUEST = 60 * 1024  # Thumbly refuses bodies over 64 KiB.
MAX_RESPONSE = 64 * 1024 * 1024  # A 20-page full-page capture fits with room to spare.
BACKOFF = (1.0, 2.0, 4.0)  # Seconds before retrying a rate-limited call.
ACCEPT = "application/json, text/event-stream"


class ToolResult:
    """One tool result: ``data`` is the structured content, ``images`` the image blocks."""

    def __init__(self, data: dict[str, Any], images: list[tuple[bytes, str]], is_error: bool):
        self.data, self.images, self.is_error = data, images, is_error

    def __repr__(self) -> str:
        return f"ToolResult(is_error={self.is_error}, keys={sorted(self.data)}, images={len(self.images)})"


def _read_bounded(response: httpx.Response) -> bytes:
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_RESPONSE:
        raise ResponseTooLarge("RESPONSE_TOO_LARGE", f"{declared} bytes from Thumbly")
    body = bytearray()
    for chunk in response.iter_bytes():
        body.extend(chunk)
        if len(body) > MAX_RESPONSE:
            raise ResponseTooLarge("RESPONSE_TOO_LARGE", f"more than {MAX_RESPONSE} bytes from Thumbly")
    return bytes(body)


def _parse(body: bytes, content_type: str) -> dict[str, Any]:
    if content_type.startswith("text/event-stream"):
        # A server may answer in SSE even for one message; take the last data line.
        data = [line[5:].strip() for line in body.decode().splitlines() if line.startswith("data:")]
        if not data:
            raise ThumblyError("INVALID_RESPONSE", "empty event stream from Thumbly")
        body = data[-1].encode()
    message = json.loads(body)
    if not isinstance(message, dict):
        raise ThumblyError("INVALID_RESPONSE", "Thumbly sent a JSON-RPC batch")
    return message


class McpClient:
    def __init__(
        self,
        http: httpx.Client,
        token: Callable[..., str],
        *,
        timeout: float,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.http, self.token, self.timeout, self.sleep = http, token, timeout, sleep
        self.ids = itertools.count(1)
        self._tools: dict[str, dict] | None = None

    def rpc(self, method: str, params: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        body = json.dumps({"jsonrpc": "2.0", "id": next(self.ids), "method": method, "params": params})
        if len(body) > MAX_REQUEST:
            raise ValueError(f"request is {len(body)} bytes; Thumbly accepts at most {MAX_REQUEST}")
        for attempt in range(2):
            headers = {
                "Authorization": f"Bearer {self.token(force_refresh=attempt > 0)}",
                "Accept": ACCEPT,
                "Content-Type": "application/json",
            }
            with self.http.stream(
                "POST", "/mcp", content=body, headers=headers, timeout=timeout or self.timeout
            ) as response:
                if response.status_code == 401 and attempt == 0:
                    continue  # Revoked or expired between our check and the call: refresh once.
                payload = _read_bounded(response)
                if response.status_code == 401:
                    raise ThumblyError("UNAUTHORIZED", "Thumbly refused this grant; approve again")
                if response.status_code >= 400:
                    raise ThumblyError(
                        f"HTTP_{response.status_code}", payload[:200].decode("utf-8", "replace")
                    )
                message = _parse(payload, response.headers.get("content-type", ""))
            if "error" in message:
                error = message["error"] if isinstance(message["error"], dict) else {}
                raise ThumblyError("RPC_ERROR", str(error.get("message", "")), detail=error)
            result = message.get("result")
            if not isinstance(result, dict):
                raise ThumblyError("INVALID_RESPONSE", "JSON-RPC result missing")
            return result
        raise AssertionError("unreachable")

    def tools(self) -> dict[str, dict]:
        """Tool name -> input schema, fetched once per client."""
        if self._tools is None:
            listed = self.rpc("tools/list", {})
            self._tools = {t["name"]: t.get("inputSchema", {}) for t in listed.get("tools", [])}
        return self._tools

    def call(self, name: str, arguments: dict[str, Any], *, timeout: float | None = None) -> ToolResult:
        """One tool call; a rate-limited call (refused before it ran) is retried with backoff."""
        for delay in (*BACKOFF, None):
            result = self.rpc("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)
            data = result.get("structuredContent")
            if not isinstance(data, dict):
                data = {}
                for block in result.get("content", []):
                    if block.get("type") == "text":
                        try:
                            data = json.loads(block["text"])
                        except ValueError:
                            data = {"text": block["text"]}
                        break
            error = data.get("error") if isinstance(data.get("error"), dict) else None
            if error and error.get("code") == "RATE_LIMITED" and delay is not None:
                self.sleep(delay)
                continue
            if error and error.get("code") == "RATE_LIMITED":
                raise RateLimited("RATE_LIMITED", "Thumbly allows 120 calls a minute", detail=error)
            images = [
                (base64.b64decode(block["data"]), str(block.get("mimeType", "")))
                for block in result.get("content", [])
                if block.get("type") == "image"
            ]
            return ToolResult(data, images, bool(result.get("isError")))
        raise AssertionError("unreachable")

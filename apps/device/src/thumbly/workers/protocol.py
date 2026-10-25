"""Version 1, length-prefixed JSON over group-restricted Unix sockets."""

import asyncio
import json
import os
import stat
import struct
from pathlib import Path

from thumbly.domain.errors import ThumblyError

VERSION = 1
MAX_REQUEST = 65536
MAX_RESPONSE = 24 * 1024 * 1024


def encode(value):
    data = json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
    if len(data) > MAX_RESPONSE:
        raise ThumblyError("IPC_TOO_LARGE")
    return struct.pack("!I", len(data)) + data


async def read(reader, maximum):
    (size,) = struct.unpack("!I", await reader.readexactly(4))
    if not 0 < size <= maximum:
        raise ThumblyError("IPC_TOO_LARGE")
    value = json.loads(await reader.readexactly(size))
    if not isinstance(value, dict):
        raise ThumblyError("IPC_INVALID")
    return value


async def rpc(path: Path, command: dict, timeout=3):
    async with asyncio.timeout(timeout):
        try:
            reader, writer = await asyncio.open_unix_connection(str(path))
        except OSError:
            raise ThumblyError("WORKER_UNAVAILABLE") from None
        try:
            writer.write(encode({"version": VERSION, **command}))
            await writer.drain()
            response = await read(reader, MAX_RESPONSE)
            if response.get("version") != VERSION:
                raise ThumblyError("IPC_VERSION")
            if "error" in response:
                error = response["error"]
                raise ThumblyError(error["code"], error.get("message", ""))
            return response["result"]
        finally:
            writer.close()
            await writer.wait_closed()


def prepare_socket(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        if not stat.S_ISSOCK(path.stat().st_mode):
            raise RuntimeError("Refusing to replace a non-socket path")
        path.unlink()


async def serve(path, dispatch):
    prepare_socket(path)
    active = 0

    async def handle(reader, writer):
        nonlocal active
        active += 1
        try:
            if active > 16:
                raise ThumblyError("WORKER_BUSY")
            request = await asyncio.wait_for(read(reader, MAX_REQUEST), 2)
            if request.pop("version", None) != VERSION:
                raise ThumblyError("IPC_VERSION")
            result = await dispatch(request)
            response = {"version": VERSION, "result": result}
        except Exception as exc:
            response = {
                "version": VERSION,
                "error": exc.as_dict() if isinstance(exc, ThumblyError) else {"code": "WORKER_FAILED"},
            }
        try:
            writer.write(encode(response))
            await asyncio.wait_for(writer.drain(), 3)
        except (OSError, TimeoutError):
            pass
        finally:
            active -= 1
            writer.close()

    server = await asyncio.start_unix_server(handle, path=str(path))
    os.chmod(path, 0o600)
    async with server:
        await server.serve_forever()

"""This Mac's network and load, for status and diagnostics. macOS owns Wi-Fi; Thumbly only reads it."""

import asyncio
import os


async def command(*args: str, timeout: float = 5) -> str:
    try:
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        output, _ = await asyncio.wait_for(process.communicate(), timeout)
    except (OSError, TimeoutError):
        return ""
    return output.decode(errors="replace").strip() if process.returncode == 0 else ""


class MacHost:
    async def _route(self) -> dict[str, str]:
        lines = (await command("route", "-n", "get", "default")).splitlines()
        return {key.strip(): value.strip() for key, _, value in (line.partition(":") for line in lines)}

    async def health(self) -> dict:
        route = await self._route()
        return {
            "wifi": ["connected" if route.get("gateway") else "disconnected"],
            "internet": "full" if route.get("gateway") else "none",
            "load_1min": os.getloadavg()[0],
            "internet_probe": "macOS default route; no independent HTTPS probe",
        }

    async def status(self) -> dict:
        route = await self._route()
        interface = route.get("interface")
        ip = await command("ipconfig", "getifaddr", interface) if interface else ""
        return {"interface": interface, "ip": ip or None, "connected": bool(route.get("gateway"))}

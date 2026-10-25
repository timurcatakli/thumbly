import asyncio


async def health(components):
    async def inspect(name, backend):
        try:
            return name, await asyncio.wait_for(backend.health(), 3)
        except Exception:
            return name, {"state": "unavailable"}

    return dict(await asyncio.gather(*(inspect(n, b) for n, b in components.items())))


async def recover(components, binding):
    async def attempt(name, backend):
        try:
            return name, await backend.recover(binding)
        except Exception as exc:
            from thumbly.domain.errors import ThumblyError

            return name, {
                "state": "unavailable",
                "error": exc.as_dict() if isinstance(exc, ThumblyError) else {"code": "RECOVERY_FAILED"},
            }

    return dict(await asyncio.gather(*(attempt(name, backend) for name, backend in components.items())))

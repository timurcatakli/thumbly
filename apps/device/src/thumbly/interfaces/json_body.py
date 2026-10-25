"""Bounded JSON request bodies for the dashboard and its owner routes."""

import json

from thumbly.domain.errors import ThumblyError


async def body(request, limit=16384):
    """The request's JSON object, read in chunks and refused past ``limit`` bytes."""
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise ThumblyError("REQUEST_TOO_LARGE", status=413)
    try:
        result = json.loads(data)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (ValueError, TypeError):
        raise ThumblyError("INVALID_JSON", status=400) from None

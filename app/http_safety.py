"""Bound actual incoming JSON bytes before routing or persistence."""
from starlette.responses import JSONResponse


class BodySizeLimitMiddleware:
    """Content-Length is only a hint; cap decoded ASGI body bytes as well."""

    def __init__(self, app, max_bytes: int = 65536):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http"
                or not scope.get("path", "").startswith("/api/")
                or scope.get("method") not in {"POST", "PATCH", "PUT", "DELETE"}):
            await self.app(scope, receive, send)
            return

        chunks = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > self.max_bytes:
                response = JSONResponse(
                    {"error": {"code": "REQUEST_TOO_LARGE",
                               "message": "요청 크기를 줄여주세요. 최대 64KB까지 처리합니다.",
                               "retryable": False}},
                    status_code=413,
                    headers={"Cache-Control": "no-store",
                             "X-Content-Type-Options": "nosniff"},
                )
                await response(scope, receive, send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break

        consumed = False

        async def bounded_receive():
            nonlocal consumed
            if consumed:
                return await receive()
            consumed = True
            return {"type": "http.request", "body": b"".join(chunks), "more_body": False}

        await self.app(scope, bounded_receive, send)

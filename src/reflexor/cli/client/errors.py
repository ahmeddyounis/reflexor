from __future__ import annotations

import httpx

CliTransportError = httpx.HTTPError
CliStatusError = httpx.HTTPStatusError
CliRequestError = httpx.RequestError

__all__ = ["CliRequestError", "CliStatusError", "CliTransportError"]

"""Small authenticated client shared by optional interfaces."""

import requests

from qareen_core.config import token


def call(path, body=None, timeout=70):
    response = requests.request(
        "GET" if body is None else "POST",
        "http://127.0.0.1:2846" + path,
        headers={"Authorization": "Bearer " + token("admin")},
        json=body,
        timeout=timeout,
    )
    try:
        result = response.json()
    except ValueError as error:
        raise RuntimeError(f"Service returned HTTP {response.status_code}") from error
    if not response.ok:
        raise RuntimeError(result.get("error", f"HTTP {response.status_code}"))
    return result

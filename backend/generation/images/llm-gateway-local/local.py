"""Overlay for non-OpenRouter upstreams: http(s) by scheme, optional bearer, no OpenRouter-only fields.

The base ``gateway.py`` is frozen (its content is part of every agent
run_fingerprint), so local endpoints reuse it unchanged and patch three
module-level hooks that it looks up at call time:

* ``_open_upstream``: plain HTTP or HTTPS by the upstream URL's scheme, the
  per-job proxy token (``LLMLAB_UPSTREAM_TOKEN``) as bearer, and none of the
  OpenRouter attribution headers;
* ``_rewrite``: the policy is applied as usual, then the OpenRouter
  ``usage: {include: true}`` accounting flag is dropped;
* ``RETRY_STATUSES``: no 500 (an Ollama 500 is usually an out-of-memory model
  load, not transient) and no 408 (the proxy owns timeouts); 504 stays so a
  proxy long-wait re-attaches to the same upstream request.
"""

# Standalone script baked into its own image (no package).
# ruff: noqa: INP001, PTH100, PTH120

import http.client
import os
import ssl
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gateway as g

TOKEN = os.environ.get("LLMLAB_UPSTREAM_TOKEN", "")
g.RETRY_STATUSES = {429, 502, 503, 504}
_base_rewrite = g._rewrite  # noqa: SLF001


def _rewrite(body):
    body, diff = _base_rewrite(body)
    body.pop("usage", None)  # OpenRouter accounting flag
    return body, diff


def _open_upstream(self, data, stream):
    u = g.UPSTREAM
    if u.scheme == "https":
        conn = http.client.HTTPSConnection(
            u.hostname,
            u.port or 443,
            timeout=g.UPSTREAM_TIMEOUT,
            context=ssl.create_default_context(),
        )
    else:
        conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=g.UPSTREAM_TIMEOUT)
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream" if stream else "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    conn.request("POST", u.path.rstrip("/") + "/chat/completions", body=data, headers=headers)
    return conn, conn.getresponse()


g._rewrite = _rewrite  # noqa: SLF001
g.Handler._open_upstream = _open_upstream  # noqa: SLF001

if __name__ == "__main__":
    g.main()

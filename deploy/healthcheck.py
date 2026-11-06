"""Authenticated readiness over verified TLS; never invokes the model."""

import json
import os
import ssl
import sys
import urllib.request


def main():
    context = ssl.create_default_context(cafile="/run/tls/ca.crt")
    request = urllib.request.Request(
        "https://hanmaum-ai:8443/health/ready/v2",
        headers={"X-Internal-Api-Key": os.environ["AI_INTERNAL_API_KEY"]},
    )
    # Do not send the reusable internal key through a process HTTP proxy.
    client = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context)
    )
    with client.open(request, timeout=3) as response:
        value = json.load(response)
    if value != {"status": "UP", "provider": "openai"}:
        raise ValueError("AI is not ready")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Healthcheck output is persisted in Docker; never print headers/configuration.
        sys.exit("AI TLS/readiness check failed")

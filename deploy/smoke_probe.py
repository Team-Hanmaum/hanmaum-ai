"""Executed only by the isolated smoke test, with synthetic credentials."""

import json
import os
import socket
import sqlite3
import ssl
import sys
import urllib.error
import urllib.request

DATABASE = "/var/lib/hanmaum-ai/receipts.sqlite3"
REQUEST_ID = "aaaaaaaa-0000-4000-8000-000000000001"


def seed():
    with sqlite3.connect(DATABASE) as connection:
        connection.execute(
            "INSERT INTO receipts VALUES (?, ?, ?, ?, ?)",
            ("v2", REQUEST_ID, "v2-json-1", "test-1", b"t" * 32),
        )


def verify():
    context = ssl.create_default_context(cafile="/run/tls/ca.crt")
    client = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context)
    )
    key = os.environ["AI_INTERNAL_API_KEY"]
    assert key == "synthetic$literal" * 3
    url = "https://hanmaum-ai:8443"
    request = urllib.request.Request(url + "/health/ready/v2", headers={"X-Internal-Api-Key": key})
    with client.open(request, timeout=3) as response:
        assert json.load(response) == {"status": "UP", "provider": "openai"}
    for path, expected in (("/health/ready/v2", 401), ("/docs", 404), ("/openapi.json", 404)):
        try:
            client.open(url + path, timeout=3)
        except urllib.error.HTTPError as error:
            assert error.code == expected
        else:
            raise AssertionError("Unexpected public route")
    for tls, name in ((ssl.create_default_context(), "hanmaum-ai"), (context, "wrong-host")):
        try:
            with socket.create_connection(("hanmaum-ai", 8443), timeout=3) as connection:
                with tls.wrap_socket(connection, server_hostname=name):
                    pass
        except ssl.SSLCertVerificationError:
            pass
        else:
            raise AssertionError("TLS verification was bypassed")
    with sqlite3.connect("file:" + DATABASE + "?mode=rw", uri=True) as connection:
        assert connection.execute("SELECT request_id FROM receipts").fetchall() == [(REQUEST_ID,)]
    print("Verified TLS chain/hostname, authentication, literal secrets and persistent receipt")


if __name__ == "__main__":
    seed() if sys.argv[1:] == ["seed"] else verify()

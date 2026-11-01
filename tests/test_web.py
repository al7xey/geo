"""Controlled loopback UI transport, no public website access."""

import http.client
import json
import re
import threading
import time

import geo_audit.web as web
from tests.test_step1_pipeline import execute_pipeline


def test_local_ui_api_and_security_boundaries(monkeypatch, tmp_path):
    runtime, result, _, _, _ = execute_pipeline(monkeypatch, tmp_path)
    entered, release = threading.Event(), threading.Event()

    async def audit(url, **kwargs):
        assert url == "https://example.com"
        assert kwargs["description"] == "We manufacture industrial components for factories."
        entered.set()
        assert release.wait(10)
        return result

    monkeypatch.setattr(web, "audit_site", audit)
    server = web.create_server(0, runtime.store.io.root.parent)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(method, path, payload=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        connection.request(method, path, body=payload, headers=headers or {})
        response = connection.getresponse()
        status, data, fields = response.status, response.read(), response.headers
        connection.close()
        return status, data, fields

    try:
        status, page, fields = request("GET", "/")
        assert status == 200 and "no-store" in fields["Cache-Control"]
        assert "frame-ancestors 'none'" in fields["Content-Security-Policy"]
        assert b"innerHTML" not in page
        token = re.search(rb"token='([^']+)'", page)[1].decode()
        headers = {"X-GEO-Token": token, "Content-Type": "application/json"}
        payload = json.dumps(
            {
                "url": "https://example.com",
                "description": "We manufacture industrial components for factories.",
            }
        )
        assert request("GET", "/", headers={"Host": "attacker.example"})[0] == 403
        assert request("POST", "/api/audit", payload)[0] == 403
        assert (
            request(
                "POST", "/api/audit", payload, {**headers, "Origin": "https://attacker.example"}
            )[0]
            == 403
        )
        assert request("POST", "/api/audit", '{"url":12}', headers)[0] == 400
        assert request("GET", "/../../private/file")[0] == 404
        assert request("POST", "/api/audit", payload, headers)[0] == 202
        assert entered.wait(5)
        assert request("POST", "/api/audit", payload, headers)[0] == 409
        release.set()
        deadline = time.monotonic() + 10
        while True:
            _, body, _ = request("GET", "/api/status")
            state = json.loads(body)
            if state["state"] != "running" or time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        assert state["state"] == "done"
        assert state["result"]["coverage"]["useful_pages"] == 1
        assert state["result"]["recommendations"]["source_step"] == 1
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(5)

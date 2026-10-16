"""Opt-in Locust self-test against our fake loopback server, never Django/MySQL."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from loadtests.order_list.core import page_ordinals, public_id, username


ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(importlib.util.find_spec("locust"), "Run with the separate Locust tool environment.")
class LocustRuntimeTests(unittest.TestCase):
    def test_real_locust_login_measure_drain_and_wrong_payload_stop(self):
        plan = {"run_id": "offline-smoke", "total_orders": 300, "accounts": 3,
                "users": [3], "page": 1, "warmup_seconds": 1, "measure_seconds": 5,
                "startup_seconds": 5, "spawn_rate": 10, "request_timeout_seconds": 1,
                "p95_limit_ms": 2000, "failure_limit": 0.01, "index_mode": "indexed"}
        accounts = [{"username": username(plan["run_id"], n), "user_id": n + 1,
                     "order_count": 100, "marker": f"marker-{n}",
                     "expected_ids": [public_id(plan["run_id"], n, i) for i in page_ordinals(100, 1)]}
                    for n in range(3)]
        by_name = {account["username"]: account for account in accounts}
        fault = [False]

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send(self, status, payload=None, cookies=()):
                data = json.dumps(payload).encode() if payload is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for cookie in cookies:
                    self.send_header("Set-Cookie", cookie)
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.headers.get("X-Order-Load-Token") != "selftest-token":
                    self.send(403)
                elif self.path == "/__order_load__/ready/":
                    self.send(200, {"suite": "project-one-order-load-v1", "run_id": plan["run_id"],
                                    "database": "project_one_order_load", "total_orders": 300,
                                    "accounts": 3, "page": 1, "index_mode": "indexed"})
                elif self.path == "/api/auth/csrf/":
                    self.send(204, cookies=("csrftoken=selftestcsrf; Path=/",))
                elif self.path == "/api/orders/?page=1":
                    cookie = self.headers.get("Cookie", "")
                    account = next((a for a in accounts if "sessionid=" + a["username"] in cookie), None)
                    if account is None:
                        self.send(403)
                        return
                    marker = "wrong-owner" if fault[0] else account["marker"]
                    self.send(200, {"count": 100, "next": "?page=2", "previous": None,
                                    "results": [{"public_id": value, "items": [
                                        {"product_name_snapshot": marker}, {"product_name_snapshot": marker},
                                    ]} for value in account["expected_ids"]]})
                else:
                    self.send(404)

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                account = by_name.get(data.get("username"))
                if (self.path != "/api/auth/login/" or account is None
                        or data.get("password") != "selftest-password"
                        or self.headers.get("X-CSRFToken") != "selftestcsrf"):
                    self.send(400)
                else:
                    self.send(200, {"id": account["user_id"], "username": account["username"], "is_staff": False},
                              cookies=("sessionid=" + account["username"] + "; Path=/", "csrftoken=rotated; Path=/"))

        try:
            server = ThreadingHTTPServer(("127.0.0.1", 18080), Handler)
        except OSError:
            self.skipTest("Dedicated loopback self-test port is already in use; no requests sent.")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for faulty in (False, True):
                fault[0] = faulty
                with self.subTest(faulty=faulty), tempfile.TemporaryDirectory() as temp:
                    folder = Path(temp)
                    (folder / "plan.json").write_text(json.dumps(plan))
                    (folder / "dataset.json").write_text(json.dumps({
                        "run_id": plan["run_id"], "total_orders": 300, "page": 1,
                        "index_mode": "indexed", "accounts": accounts,
                    }))
                    env = {**os.environ, "ORDER_LOAD_STAGE": "0", "ORDER_LOAD_ARTIFACTS": temp,
                           "ORDER_LOAD_TOKEN": "selftest-token", "ORDER_LOAD_PASSWORD": "selftest-password",
                           "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9"}
                    result = subprocess.run([
                        sys.executable, "-m", "locust", "-f", str(ROOT / "loadtests/order_list/locustfile.py"),
                        "--headless", "--host", "http://127.0.0.1:18080", "--users", "3", "--spawn-rate", "10",
                        "--run-time", "20", "--stop-timeout", "2", "--exit-code-on-error", "0", "--only-summary",
                    ], cwd=ROOT, env=env, capture_output=True, text=True, timeout=35)
                    output = folder / "stage-01-u3" / "stage.json"
                    self.assertTrue(output.exists(), result.stdout + result.stderr)
                    summary = json.loads(output.read_text())
                    if faulty:
                        self.assertFalse(summary["valid_for_capacity"])
                        self.assertEqual(summary["stop_reason"], "response_correctness_failed")
                    else:
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertEqual(summary["status"], "complete")
                        self.assertGreaterEqual(summary["sample_count"], 100)
                        self.assertEqual(summary["failure_count"], 0)
                        self.assertEqual(summary["ready_users"], 3)
                    raw = (output.parent / "requests.jsonl").read_text()
                    self.assertNotIn("selftest-password", raw)
                    self.assertNotIn("selftest-token", raw)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()

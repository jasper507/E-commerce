import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import order_load


class ContainerEdgeTests(unittest.TestCase):
    def test_context_cannot_override_a_checked_local_host(self):
        with patch.dict(os.environ, {"DOCKER_HOST": "npipe:////./pipe/docker_engine",
                                     "DOCKER_CONTEXT": "remote-context"}), (
            patch.object(order_load, "capture")
        ) as command:
            with self.assertRaises(RuntimeError):
                order_load.require_local_engine()
            command.assert_not_called()

    def test_naturally_finished_client_is_not_a_server_failure(self):
        def value(service, running=True):
            return json.dumps({"labels": {"com.docker.compose.service": service},
                               "state": {"OOMKilled": False, "Running": running}, "restarts": 0})
        responses = ["db-id app-id client-id", value("db"), value("app"), value("loadgen", False),
                     '{"Name":"db"}\n{"Name":"app"}\n{"Name":"client"}']
        with patch.object(order_load, "capture", side_effect=responses):
            snapshot = order_load.sample_resources("project-one-order-load-safe-run")
        self.assertEqual(len(snapshot["states"]), 3)

    def test_host_stage_accepts_normal_client_exit_after_complete_measurement(self):
        plan = {"run_id": "safe-run", "users": [1], "total_orders": 100,
                "page": 1, "index_mode": "indexed", "startup_seconds": 180,
                "warmup_seconds": 1, "measure_seconds": 5,
                "request_timeout_seconds": 5, "spawn_rate": 2}
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "safe-run"
            folder.mkdir()
            (folder / "execution-source.json").write_text(json.dumps({
                "source_commit": "reviewed-source", "image_id": "sha256:reviewed",
            }))
            (folder / "dataset.json").write_text("{}")
            process = Mock(returncode=0)
            process.poll.side_effect = [None, None, None, 0, 0]

            def launch(*args, **kwargs):
                stage_folder = folder / "stage-01-u1"
                stage_folder.mkdir()
                stage = {"users": 1, "total_orders": 100, "page": 1,
                         "index_mode": "indexed", "status": "complete",
                         "valid_for_capacity": True, "client_cpu_saturated": False,
                         "sample_count": 100, "success_rps": 20, "p95_ms": 10,
                         "failure_rate": 0, "measurement_start_unix": 100,
                         "measurement_end_unix": 105}
                (stage_folder / "stage.json").write_text(json.dumps(stage))
                return process

            snapshots = [{"timestamp": timestamp, "stats": [], "states": [
                {"service": service, "running": timestamp <= 105 or service != "loadgen",
                 "oom": False, "restarts": 0}
                for service in ("app", "db", "loadgen")
            ]} for timestamp in (101, 103, 106)]
            with patch.object(order_load, "resource_gate"), (
                patch.object(order_load, "capture", side_effect=[
                    "sha256:reviewed", "app-id", "sha256:reviewed", "app\ndb",
                ])
            ), patch.object(order_load.subprocess, "Popen", side_effect=launch), (
                patch.object(order_load, "sample_resources", side_effect=snapshots)
            ), patch.object(order_load.time, "sleep"), patch("builtins.print"):
                order_load.run_stages(folder, plan, execute=True)
            report = json.loads((folder / "report.json").read_text())
            self.assertEqual(report["stages"][0]["status"], "complete")
            self.assertTrue(report["stages"][0]["valid_for_capacity"])
            self.assertEqual(report["stages"][0]["resource_samples"], 2)


if __name__ == "__main__":
    unittest.main()

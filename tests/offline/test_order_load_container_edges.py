import json
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

from scripts import order_load


def stage_plan(users):
    return {"run_id": "safe-run", "users": users, "total_orders": 100,
            "page": 1, "index_mode": "indexed", "startup_seconds": 180,
            "warmup_seconds": 1, "measure_seconds": 5,
            "request_timeout_seconds": 5, "spawn_rate": 2}


@contextmanager
def prepared_target():
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp) / "safe-run"
        folder.mkdir()
        (folder / "execution-source.json").write_text(json.dumps({
            "source_commit": "reviewed-source", "image_id": "sha256:reviewed",
        }))
        (folder / "dataset.json").write_text("{}")
        yield folder


def completed_stage_launch(folder, process):
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
    return launch


def resource_snapshots(timestamps):
    return [{"timestamp": timestamp, "stats": [], "states": [
        {"service": service, "running": timestamp <= 105 or service != "loadgen",
         "oom": False, "restarts": 0}
        for service in ("app", "db", "loadgen")
    ]} for timestamp in timestamps]


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
        plan = stage_plan([1])
        with prepared_target() as folder:
            process = Mock(returncode=0)
            process.poll.side_effect = [None, None, None, 0, 0]
            snapshots = resource_snapshots((101, 103, 106))
            with patch.object(order_load, "resource_gate"), (
                patch.object(order_load, "capture", side_effect=[
                    "reviewed-controller", "sha256:reviewed", "app-id", "sha256:reviewed", "app\ndb",
                ])
            ), patch.object(order_load.subprocess, "Popen", side_effect=completed_stage_launch(folder, process)), (
                patch.object(order_load, "sample_resources", side_effect=snapshots)
            ), patch.object(order_load, "available_memory", return_value=8 * 1024**3), (
                patch.object(order_load.time, "sleep")
            ), patch("builtins.print"):
                order_load.run_stages(folder, plan, execute=True)
            report = json.loads((folder / "report.json").read_text())
            self.assertEqual(report["stages"][0]["status"], "complete")
            self.assertTrue(report["stages"][0]["valid_for_capacity"])
            self.assertEqual(report["stages"][0]["resource_samples"], 2)
            self.assertEqual(report["controller_commit"], "reviewed-controller")

    def test_low_host_memory_invalidates_complete_stage_and_prevents_next_load_level(self):
        plan = stage_plan([1, 5])
        with prepared_target() as folder:
            process = Mock(returncode=0)
            process.poll.side_effect = [None, None, None, None]
            snapshots = resource_snapshots((101, 103, 104))
            memory = [8 * 1024**3, 8 * 1024**3, 512 * 1024**2]
            with patch.object(order_load, "resource_gate"), (
                patch.object(order_load, "capture", side_effect=[
                    "reviewed-controller", "sha256:reviewed", "app-id", "sha256:reviewed", "app\ndb",
                ])
            ), patch.object(
                order_load.subprocess, "Popen", side_effect=completed_stage_launch(folder, process),
            ) as launch, patch.object(order_load, "sample_resources", side_effect=snapshots), (
                patch.object(order_load, "available_memory", side_effect=memory)
            ), patch.object(order_load, "stop_owned_client") as stop, (
                patch.object(order_load.time, "sleep")
            ), patch("builtins.print"):
                order_load.run_stages(folder, plan, execute=True)

            report = json.loads((folder / "report.json").read_text())
            self.assertEqual(len(report["stages"]), 1)
            result = report["stages"][0]
            self.assertEqual(result["users"], 1)
            self.assertEqual(result["status"], "stopped")
            self.assertFalse(result["valid_for_capacity"])
            self.assertEqual(result["host_stop_reason"], "host_memory_below_1gib")
            # Enough resource samples and a successful client exit cannot hide a host memory stop.
            self.assertEqual(result["resource_samples"], 3)
            self.assertEqual(report["analysis"]["eligible_stage_count"], 0)
            launch.assert_called_once()
            stop.assert_called_once_with(
                "project-one-order-load-safe-run-stage-1", "project-one-order-load-safe-run",
            )
            process.wait.assert_called_once_with(timeout=15)
            recorded = [json.loads(line) for line in (
                folder / "stage-01-resources.jsonl"
            ).read_text().splitlines()]
            self.assertEqual([row["host_available_memory_bytes"] for row in recorded], memory)
            self.assertFalse((folder / "stage-02-u5").exists())


if __name__ == "__main__":
    unittest.main()

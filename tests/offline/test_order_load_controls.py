import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import order_load


class ControlTests(unittest.TestCase):
    def test_compose_project_is_explicit_and_cannot_follow_host_project_name(self):
        with patch.dict(os.environ, {"COMPOSE_PROJECT_NAME": "unrelated"}):
            command = order_load.compose(Path("safe-run"))
        self.assertEqual(command[command.index("--project-name") + 1],
                         "project-one-order-load-safe-run")

    def test_environment_file_overrides_host_run_and_rejects_unknown_keys(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=False):
            folder = Path(temp)
            values = {"ORDER_LOAD_RUN": "safe-run", "ORDER_LOAD_UID": "10001", "ORDER_LOAD_GID": "10001"}
            for suffix in ("MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD", "DJANGO_SECRET", "TOKEN", "PASSWORD"):
                values["ORDER_LOAD_" + suffix] = "a" * 24
            path = folder / ".env"
            path.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
            os.environ["ORDER_LOAD_RUN"] = "unrelated"
            os.environ["COMPOSE_PROJECT_NAME"] = "unrelated"
            order_load.load_environment(folder, {"run_id": "safe-run"})
            self.assertEqual(os.environ["ORDER_LOAD_RUN"], "safe-run")
            self.assertNotIn("COMPOSE_PROJECT_NAME", os.environ)
            path.write_text(path.read_text() + "MYSQL_DATABASE=project_one\n")
            with self.assertRaises(ValueError):
                order_load.load_environment(folder, {"run_id": "safe-run"})

    def test_low_memory_stops_before_docker_is_contacted(self):
        with patch.object(order_load, "available_memory", return_value=1024**3), (
            patch.object(order_load, "capture")
        ) as call:
            with self.assertRaises(RuntimeError):
                order_load.resource_gate({"total_orders": 10_000})
            call.assert_not_called()

    def test_remote_docker_target_is_rejected(self):
        with patch.dict(os.environ, {"DOCKER_HOST": "ssh://example.invalid"}), (
            patch.object(order_load, "capture", return_value="test-context")
        ):
            with self.assertRaises(RuntimeError):
                order_load.require_local_engine()

    def test_missing_target_container_is_a_hard_stop(self):
        row = json.dumps({"labels": {"com.docker.compose.service": "db"},
                          "state": {"OOMKilled": False, "Running": True}, "restarts": 0})
        with patch.object(order_load, "capture", side_effect=["db-id", row]):
            with self.assertRaises(order_load.ResourceStop):
                order_load.sample_resources("project-one-order-load-safe-run")

    def test_stopped_or_oom_target_cannot_disappear_from_sampling(self):
        def value(service, oom=False):
            return json.dumps({"labels": {"com.docker.compose.service": service},
                               "state": {"OOMKilled": oom, "Running": not oom}, "restarts": 0})
        with patch.object(order_load, "capture", side_effect=["db-id app-id", value("db"), value("app", True)]) as call:
            with self.assertRaises(order_load.ResourceStop):
                order_load.sample_resources("project-one-order-load-safe-run")
            self.assertIn("--all", call.call_args_list[0].args[0])


if __name__ == "__main__":
    unittest.main()

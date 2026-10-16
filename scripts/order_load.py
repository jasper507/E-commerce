"""Plan first; explicit --execute is required for Docker, data or load changes."""

import argparse
import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from loadtests.order_list.core import DATABASE, username, validate_plan
from loadtests.order_list.metrics import analyze_stages, summarize_samples

ARTIFACTS = ROOT / "artifacts" / "order-load"


def folder_for(run_id):
    username(run_id, 0)  # Same strict namespace validation as fixture generation.
    return ARTIFACTS / run_id


def compose(folder):
    username(folder.name, 0)
    return ["docker", "compose", "--project-name", "project-one-order-load-" + folder.name,
            "--env-file", str(folder / ".env"),
            "--file", str(ROOT / "compose.order-load.yaml")]


def load_environment(folder, plan):
    values = {}
    for line in (folder / ".env").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            name, value = line.split("=", 1)
            if name in values:
                raise ValueError("Duplicate variable in dedicated environment file.")
            values[name] = value
    required = {"ORDER_LOAD_RUN", "ORDER_LOAD_UID", "ORDER_LOAD_GID", *[
        "ORDER_LOAD_" + key for key in ("MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD", "DJANGO_SECRET", "TOKEN", "PASSWORD")
    ]}
    if set(values) != required or values["ORDER_LOAD_RUN"] != plan["run_id"]:
        raise ValueError("Dedicated environment file and plan disagree.")
    for key, value in values.items():
        if key.endswith(("_UID", "_GID")):
            if not value.isdigit() or (key.endswith("_UID") and int(value) == 0):
                raise ValueError("Invalid dedicated non-root identity.")
        elif key != "ORDER_LOAD_RUN" and not re.fullmatch(r"[A-Za-z0-9_-]{20,}", value):
            raise ValueError("Invalid generated local credential format.")
    for key in ("COMPOSE_PROJECT_NAME", "COMPOSE_ENV_FILES", "COMPOSE_FILE", "COMPOSE_PROFILES"):
        os.environ.pop(key, None)
    os.environ.update(values)  # Explicit reviewed file wins over unrelated host variables.


def capture(command):
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError("Command failed: " + " ".join(command[:3]))
    return result.stdout.strip()


def available_memory():
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                        *[(name, ctypes.c_ulonglong) for name in (
                            "total", "available", "page_total", "page_available",
                            "virtual_total", "virtual_available", "extended")]]
        value = MemoryStatus()
        value.length = ctypes.sizeof(value)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
            raise RuntimeError("Cannot read available physical memory.")
        return value.available
    if sys.platform.startswith("linux"):
        rows = Path("/proc/meminfo").read_text().splitlines()
        return int(next(row.split()[1] for row in rows if row.startswith("MemAvailable:"))) * 1024
    raise RuntimeError("Available-memory preflight supports Windows and Linux only.")


def host_memory_requirement(plan, *, target_prepared=False):
    users = plan.get("users", [])
    small_smoke = (plan["total_orders"] <= 10_000 and users
                   and max(users) <= 20 and plan.get("measure_seconds", 301) <= 30)
    # The prepared target already occupies memory reserved before construction.
    return ((2 if target_prepared else 3) if small_smoke else 5) * 1024**3


def resource_gate(plan, *, target_prepared=False):
    minimum_disk = max(10, plan["total_orders"] / 1_000_000 * 15) * 1024**3
    minimum_memory = host_memory_requirement(plan, target_prepared=target_prepared)
    if available_memory() < minimum_memory:
        raise RuntimeError(f"Need at least {minimum_memory // 1024**3} GiB available host memory before this experiment.")
    if shutil.disk_usage(ROOT).free < minimum_disk:
        raise RuntimeError("Insufficient disk reserve for the selected dataset size.")
    require_local_engine()
    engine_memory = int(capture(["docker", "info", "--format", "{{.MemTotal}}"]))
    if engine_memory < 5 * 1024**3:
        raise RuntimeError("Docker engine must have at least 5 GiB total memory.")


def require_local_engine():
    override = os.environ.get("DOCKER_HOST")
    if override and os.environ.get("DOCKER_CONTEXT"):
        raise RuntimeError("Unset conflicting DOCKER_HOST/DOCKER_CONTEXT before local experiments.")
    context = capture(["docker", "context", "show"])
    endpoint = override or json.loads(capture([
        "docker", "context", "inspect", context, "--format", "{{json .Endpoints.docker.Host}}",
    ]))
    if not endpoint.startswith(("npipe://", "unix://")):
        raise RuntimeError("Only a local Docker pipe/socket is permitted for this experiment.")


def new_plan(args):
    plan = validate_plan({
        "run_id": args.run, "total_orders": args.orders, "accounts": args.accounts,
        "users": [int(n) for n in args.users.split(",")], "page": args.page,
        "warmup_seconds": args.warmup, "measure_seconds": args.duration,
        "startup_seconds": 180, "spawn_rate": 2,
        "request_timeout_seconds": 5, "p95_limit_ms": 2000,
        "failure_limit": 0.01, "index_mode": args.index_mode,
    })
    if os.name != "nt" and os.getuid() == 0:
        raise RuntimeError("Create plans as a regular host user so bind-mounted artifacts stay non-root.")
    folder = folder_for(args.run)
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    values = {"ORDER_LOAD_RUN": args.run,
              "ORDER_LOAD_UID": str(os.getuid() if os.name != "nt" else 10001),
              "ORDER_LOAD_GID": str(os.getgid() if os.name != "nt" else 10001)}
    for suffix in ("MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD", "DJANGO_SECRET", "TOKEN", "PASSWORD"):
        values["ORDER_LOAD_" + suffix] = secrets.token_urlsafe(32)
    secret_file = folder / ".env"
    with secret_file.open("x", encoding="utf-8") as stream:
        stream.write("# Generated for this disposable local dataset only. Do not share.\n")
        stream.writelines(f"{name}={value}\n" for name, value in values.items())
    secret_file.chmod(0o600)
    metadata = {"created_utc": datetime.now(timezone.utc).isoformat(),
                "plan_created_at_commit": capture(["git", "rev-parse", "HEAD"]),
                "target": "http://127.0.0.1:18080", "database": DATABASE,
                "distribution": "uniform orders per account; two items per order",
                "resources": "db: 2 CPU/2GiB; app: 2 CPU/768MiB/2 sync workers; loadgen: 1 CPU/768MiB"}
    (folder / "environment.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps({"dry_run": True, "plan": plan, "artifacts": str(folder),
                      "related_rows": {"orders": plan["total_orders"],
                                       "addresses": plan["total_orders"],
                                       "items": plan["total_orders"] * 2}}, indent=2))


def prepare(folder, plan, execute):
    commands = [compose(folder) + ["build", "app"],
                compose(folder) + ["up", "--detach", "--wait", "--wait-timeout", "180", "db"],
                compose(folder) + ["run", "--rm", "--no-deps", "app", "python", "-m",
                                   "loadtests.order_list.seed", "--execute", "--confirm-database", DATABASE],
                compose(folder) + ["up", "--detach", "--wait", "--wait-timeout", "120", "app"]]
    if not execute:
        print(json.dumps({"dry_run": True, "commands": commands}, indent=2))
        return
    resource_gate(plan)
    relevant = ["backend", "loadtests", "requirements", "docker/order-load", "compose.order-load.yaml", "scripts/order_load.py"]
    if capture(["git", "status", "--porcelain", "--", *relevant]):
        raise RuntimeError("Commit/review the candidate source before building a reproducible target.")
    if (folder / "execution-source.json").exists():
        raise RuntimeError("This preparation was already attempted; use a fresh reviewed run namespace.")
    project = "project-one-order-load-" + plan["run_id"]
    if capture(compose(folder) + ["ps", "--all", "--quiet"]):
        raise RuntimeError("This namespace already has containers; choose a fresh run ID.")
    if capture(["docker", "volume", "ls", "--filter", "label=com.docker.compose.project=" + project,
                "--format", "{{.Name}}"]):
        raise RuntimeError("This namespace already has a volume; choose a fresh run ID.")
    for index, command in enumerate(commands):
        subprocess.run(command, cwd=ROOT, check=True)
        if index == 0:
            record = {"source_commit": capture(["git", "rev-parse", "HEAD"]),
                      "image_id": capture(["docker", "image", "inspect",
                                           "project-one-order-load:" + plan["run_id"], "--format", "{{.Id}}"])}
            with (folder / "execution-source.json").open("x", encoding="utf-8") as stream:
                json.dump(record, stream, indent=2)


class ResourceStop(RuntimeError):
    pass


def sample_resources(project):
    ids = capture(["docker", "ps", "--all", "--filter", "label=com.docker.compose.project=" + project,
                   "--format", "{{.ID}}"]).split()
    if not ids:
        raise RuntimeError("No experiment containers are running.")
    states = []
    for identifier in ids:
        value = json.loads(capture(["docker", "inspect", "--format",
                                   '{"state":{{json .State}},"restarts":{{.RestartCount}},"labels":{{json .Config.Labels}}}',
                                   identifier]))
        states.append({"service": value["labels"].get("com.docker.compose.service"),
                       "oom": value["state"]["OOMKilled"], "running": value["state"]["Running"],
                       "restarts": value["restarts"]})
    for service in ("app", "db"):
        if sum(s["service"] == service for s in states) != 1:
            raise ResourceStop("target_container_missing_or_duplicated")
    if any(s["oom"] or s["restarts"]
           or (s["service"] in {"app", "db"} and not s["running"]) for s in states):
        raise ResourceStop("container_oom_restart_or_stop")
    stats = [json.loads(line) for line in capture(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}", *ids]).splitlines()]
    return {"timestamp": time.time(), "stats": stats, "states": states}


def stop_owned_client(name, project):
    try:
        labels = json.loads(capture(["docker", "inspect", "--format", "{{json .Config.Labels}}", name]))
        if (labels.get("com.docker.compose.project") == project
                and labels.get("com.docker.compose.service") == "loadgen"):
            subprocess.run(["docker", "stop", "--time", "5", name], cwd=ROOT,
                           capture_output=True, timeout=15, check=False)
    except (RuntimeError, subprocess.SubprocessError):
        pass


def run_stages(folder, plan, execute):
    if not execute:
        print(json.dumps({"dry_run": True, "users": plan["users"], "page": plan["page"],
                          "total_orders": plan["total_orders"], "requires": "prepared independent target"}, indent=2))
        return
    resource_gate(plan, target_prepared=True)
    controller_commit = capture(["git", "rev-parse", "HEAD"])
    source = json.loads((folder / "execution-source.json").read_text(encoding="utf-8"))
    if capture(["docker", "image", "inspect", "project-one-order-load:" + plan["run_id"],
                "--format", "{{.Id}}"]) != source["image_id"]:
        raise RuntimeError("The prepared load image was replaced; refusing a different source target.")
    app_ids = capture(compose(folder) + ["ps", "--all", "--quiet", "app"]).split()
    if (len(app_ids) != 1 or capture([
        "docker", "inspect", "--format", "{{.Image}}", app_ids[0],
    ]) != source["image_id"]):
        raise RuntimeError("Running app image does not match the prepared source record.")
    if not (folder / "dataset.json").exists() or list(folder.glob("stage-*")):
        raise RuntimeError("Need a complete fresh dataset and no previous stage artifacts.")
    if set(capture(compose(folder) + ["ps", "--status", "running", "--services"]).split()) != {"app", "db"}:
        raise RuntimeError("Expected only the dedicated app and db before starting load.")
    project = "project-one-order-load-" + plan["run_id"]
    stages = []
    for index, users in enumerate(plan["users"]):
        name = f"{project}-stage-{index + 1}"
        hard_seconds = (plan["startup_seconds"] + plan["warmup_seconds"]
                        + plan["measure_seconds"] + plan["request_timeout_seconds"] + 30)
        command = compose(folder) + [
            "run", "--rm", "--no-deps", "--name", name, "-e", f"ORDER_LOAD_STAGE={index}",
            "loadgen", "-f", "/app/loadtests/order_list/locustfile.py", "--headless",
            "--host", "http://app:8000", "--users", str(users), "--spawn-rate", str(plan["spawn_rate"]),
            "--run-time", str(hard_seconds), "--stop-timeout", "5",
            "--exit-code-on-error", "0",
            "--csv", f"/artifacts/locust-stage-{index + 1:02d}", "--only-summary",
        ]
        snapshots, stop_reason = [], None
        console_path = folder / f"stage-{index + 1:02d}-console.log"
        with console_path.open("x", encoding="utf-8") as console, (
            folder / f"stage-{index + 1:02d}-resources.jsonl"
        ).open("x", encoding="utf-8") as resources:
            process = subprocess.Popen(command, cwd=ROOT, stdout=console, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + hard_seconds + 30
            try:
                while process.poll() is None:
                    try:
                        row = sample_resources(project)
                        row["host_available_memory_bytes"] = available_memory()
                        snapshots.append(row)
                        resources.write(json.dumps(row) + "\n")
                        resources.flush()
                        if row["host_available_memory_bytes"] < 1024**3:
                            stop_reason = "host_memory_below_1gib"
                            break
                    except ResourceStop as exc:
                        stop_reason = str(exc)
                        resources.write(json.dumps({"timestamp": time.time(), "stop_reason": stop_reason}) + "\n")
                        break
                    except (RuntimeError, subprocess.SubprocessError, ValueError):
                        resources.write(json.dumps({"timestamp": time.time(), "sampling_error": True}) + "\n")
                    if time.monotonic() >= deadline:
                        stop_reason = "host_hard_deadline"
                        break
                    time.sleep(2)
            finally:
                if process.poll() is None:
                    stop_owned_client(name, project)
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        process.wait(timeout=10)
        stage_path = folder / f"stage-{index + 1:02d}-u{users}" / "stage.json"
        if stage_path.exists():
            stage = json.loads(stage_path.read_text(encoding="utf-8"))
        else:
            stage = {**summarize_samples([], plan["measure_seconds"]), "status": "failed",
                     "valid_for_capacity": False, "client_cpu_saturated": False,
                     "users": users, "total_orders": plan["total_orders"], "page": plan["page"],
                     "index_mode": plan["index_mode"], "stop_reason": "missing_stage_result"}
        start, end = stage.get("measurement_start_unix"), stage.get("measurement_end_unix")
        steady = [r for r in snapshots if start is not None and start <= r["timestamp"] <= end
                  and {s["service"] for s in r["states"]} >= {"app", "db", "loadgen"}]
        stage["resource_samples"] = len(steady)
        if stop_reason or process.returncode or len(steady) < 2:
            stage["valid_for_capacity"] = False
            stage["status"] = "stopped"
            stage["host_stop_reason"] = stop_reason or ("client_exit_error" if process.returncode else "resource_evidence_missing")
        stages.append(stage)
        print(json.dumps({key: stage.get(key) for key in
                          ("users", "status", "success_rps", "p95_ms", "failure_rate", "resource_samples")}), flush=True)
        if stage["status"] != "complete" or not stage["valid_for_capacity"]:
            break
        time.sleep(5)
    report = {"plan": plan, "source": source, "controller_commit": controller_commit,
              "stages": stages, "analysis": analyze_stages(stages)}
    with (folder / "report.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan_parser = sub.add_parser("plan", help="Write a plan and ignored generated local credentials; no Docker.")
    plan_parser.add_argument("--run", required=True)
    plan_parser.add_argument("--orders", type=int, default=10_000)
    plan_parser.add_argument("--accounts", type=int, default=20)
    plan_parser.add_argument("--users", default="1,5,10,20")
    plan_parser.add_argument("--page", type=int, default=1)
    plan_parser.add_argument("--warmup", type=int, default=10)
    plan_parser.add_argument("--duration", type=int, default=30)
    plan_parser.add_argument("--index-mode", choices=("indexed", "without_read_index"), default="indexed")
    for command in ("prepare", "run", "stop"):
        item = sub.add_parser(command)
        item.add_argument("--run", required=True)
        item.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.command == "plan":
        new_plan(args)
        return
    folder = folder_for(args.run)
    plan = validate_plan(json.loads((folder / "plan.json").read_text(encoding="utf-8")))
    if plan["run_id"] != args.run:
        parser.error("Run folder and plan namespace differ.")
    load_environment(folder, plan)
    if args.command == "prepare":
        prepare(folder, plan, args.execute)
    elif args.command == "run":
        run_stages(folder, plan, args.execute)
    elif args.execute:
        require_local_engine()
        subprocess.run(compose(folder) + ["stop"], cwd=ROOT, check=True)
    else:
        print(json.dumps({"dry_run": True, "command": compose(folder) + ["stop"]}))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"order-load stopped: {exc}", file=sys.stderr)
        raise SystemExit(2)

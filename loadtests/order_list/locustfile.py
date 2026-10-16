"""One bounded headless stage. Setup and warmup are excluded from our report."""

from collections import deque
import json
import os
from pathlib import Path
import time

import gevent
from locust import HttpUser, constant, events, task
from locust.exception import StopUser
from locust.runners import LocalRunner
import psutil
import requests

from loadtests.order_list.core import DATABASE, validate_plan, validate_response, validate_target
from loadtests.order_list.metrics import summarize_samples


STATE = None
MAX_SAMPLES = 100_000


def preflight(origin, plan, manifest, token):
    with requests.Session() as session:
        session.trust_env = False
        response = session.get(origin + "/__order_load__/ready/",
                               headers={"X-Order-Load-Token": token},
                               timeout=5, allow_redirects=False)
    expected = {"suite": "project-one-order-load-v1", "run_id": plan["run_id"],
                "database": DATABASE, "total_orders": plan["total_orders"],
                "accounts": plan["accounts"], "page": plan["page"],
                "index_mode": plan["index_mode"]}
    if response.status_code != 200 or response.json() != expected:
        raise ValueError("Target identity or dataset does not match the reviewed plan.")
    if (manifest["run_id"] != plan["run_id"] or manifest["page"] != plan["page"]
            or manifest["index_mode"] != plan["index_mode"]
            or manifest["total_orders"] != plan["total_orders"]
            or len(manifest["accounts"]) != plan["accounts"]):
        raise ValueError("Local dataset manifest does not match the plan.")


def finish(environment, reason):
    state = STATE
    if state is None or state["finished"]:
        return
    state["finished"] = True
    state["phase"] = "drain"
    state["raw"].close()
    start = state["measure_start"]
    duration = state["measure_duration"] if start else 0.001
    metrics = summarize_samples(state["samples"], duration)
    reason = state["fatal"] or reason
    if metrics["sample_count"] and (
        metrics["p95_ms"] > state["plan"]["p95_limit_ms"]
        or metrics["failure_rate"] > state["plan"]["failure_limit"]
    ):
        reason = state["fatal"] or "final_latency_or_failure_limit"
    complete = (reason == "completed" and state["inflight"] == 0
                and metrics["sample_count"] >= 100 and not state["cpu_saturated"])
    summary = {**metrics, "users": state["users"],
               "total_orders": state["plan"]["total_orders"], "page": state["plan"]["page"],
               "index_mode": state["plan"]["index_mode"],
               "status": "complete" if complete else "stopped",
               "stop_reason": reason, "valid_for_capacity": complete,
               "ready_users": state["ready"], "undrained_requests": state["inflight"],
               "measurement_seconds": duration, "client_cpu_saturated": state["cpu_saturated"],
               "measurement_start_unix": state["measure_start_unix"],
               "measurement_end_unix": (state["measure_start_unix"] + duration) if start else None,
               "client_cpu_max_percent": state["cpu_max"], "resource_samples": 0,
               "latency_cohort": "requests started in steady window; late completions included",
               "throughput_window": "cohort completions inside steady window only"}
    with (state["folder"] / "stage.json").open("x", encoding="utf-8") as stream:
        json.dump(summary, stream, indent=2)
    if not complete:
        environment.process_exit_code = 2


def watch(environment):
    state, plan = STATE, STATE["plan"]
    process = psutil.Process()
    process.cpu_percent()
    deadline = time.monotonic() + plan["startup_seconds"]
    reason = "completed"
    while state["ready"] != state["users"]:
        if state["fatal"] or time.monotonic() >= deadline:
            finish(environment, state["fatal"] or "login_startup_timeout")
            environment.runner.quit()
            return
        gevent.sleep(0.2)
    state["phase"] = "warmup"
    warmup_end = time.monotonic() + plan["warmup_seconds"]
    while time.monotonic() < warmup_end:
        if state["fatal"]:
            finish(environment, state["fatal"])
            environment.runner.quit()
            return
        gevent.sleep(0.2)
    process.cpu_percent()
    state["measure_start"] = time.monotonic()
    state["measure_start_unix"] = time.time()
    state["measure_end"] = state["measure_start"] + plan["measure_seconds"]
    state["phase"] = "measure"
    strikes = cpu_strikes = 0
    while time.monotonic() < state["measure_end"]:
        gevent.sleep(1)
        cpu = process.cpu_percent()
        state["cpu_max"] = max(state["cpu_max"], cpu)
        cpu_strikes = cpu_strikes + 1 if cpu >= 85 else 0
        if state["fatal"] or cpu_strikes >= 3 or len(state["samples"]) >= MAX_SAMPLES:
            state["cpu_saturated"] = cpu_strikes >= 3
            reason = state["fatal"] or ("client_cpu_saturated" if state["cpu_saturated"] else "sample_limit")
            break
        recent = list(state["recent"])
        if len(recent) >= 100:
            summary = summarize_samples(recent, 1)
            exceeded = (summary["p95_ms"] > plan["p95_limit_ms"]
                        or summary["failure_rate"] > plan["failure_limit"])
            strikes = strikes + 1 if exceeded else 0
            if strikes >= 3:
                reason = "latency_or_failure_stop_limit"
                break
    state["measure_end"] = min(state["measure_end"], time.monotonic())
    state["measure_duration"] = max(0.001, state["measure_end"] - state["measure_start"])
    state["phase"] = "drain"
    # No new measured requests are admitted after this point. Keep the slow tail.
    drain_end = time.monotonic() + plan["request_timeout_seconds"] + 2
    while state["inflight"] and time.monotonic() < drain_end:
        gevent.sleep(0.1)
    if state["inflight"]:
        reason = "undrained_requests"
    finish(environment, reason)
    environment.runner.quit()


def initialize(environment):
    global STATE
    if not isinstance(environment.runner, LocalRunner):
        raise SystemExit("This bounded suite currently supports a single load generator only.")
    root = Path(os.environ.get("ORDER_LOAD_ARTIFACTS", "/artifacts"))
    plan = validate_plan(json.loads((root / "plan.json").read_text(encoding="utf-8")))
    stage_index = int(os.environ["ORDER_LOAD_STAGE"])
    if not 0 <= stage_index < len(plan["users"]):
        raise SystemExit("Invalid stage index.")
    origin = validate_target(environment.host)
    manifest = json.loads((root / "dataset.json").read_text(encoding="utf-8"))
    preflight(origin, plan, manifest, os.environ["ORDER_LOAD_TOKEN"])
    users = plan["users"][stage_index]
    if environment.parsed_options.num_users != users:
        raise SystemExit("Locust users differ from the stage plan.")
    folder = root / f"stage-{stage_index + 1:02d}-u{users}"
    folder.mkdir(exist_ok=False)
    STATE = {"plan": plan, "users": users, "folder": folder,
             "accounts": iter(manifest["accounts"]), "ready": 0,
             "phase": "startup", "fatal": None, "finished": False,
             "measure_start": None, "measure_end": None, "samples": [],
             "measure_start_unix": None, "measure_duration": plan["measure_seconds"],
             "recent": deque(maxlen=1000), "inflight": 0,
             "cpu_max": 0.0, "cpu_saturated": False,
             "raw": (folder / "requests.jsonl").open("x", encoding="utf-8", buffering=65536)}
    gevent.spawn(watch, environment)


@events.test_start.add_listener
def start(environment, **kwargs):
    try:
        initialize(environment)
    except (ValueError, OSError, KeyError, requests.RequestException):
        environment.process_exit_code = 2
        raise SystemExit("Order-load preflight failed; no load stage was admitted.")


@events.quitting.add_listener
def quitting(environment, **kwargs):
    finish(environment, "external_stop_or_hard_deadline")


class OrderListUser(HttpUser):
    wait_time = constant(0.1)

    def on_start(self):
        self.account = next(STATE["accounts"])
        self.client.trust_env = False
        self.client.headers.update({"X-Order-Load-Token": os.environ["ORDER_LOAD_TOKEN"]})
        timeout = STATE["plan"]["request_timeout_seconds"]
        try:
            with self.client.get("/api/auth/csrf/", name="setup/csrf", timeout=timeout,
                                 allow_redirects=False, catch_response=True) as response:
                if response.status_code != 204 or not self.client.cookies.get("csrftoken"):
                    response.failure("csrf_setup_failed")
                    raise ValueError("csrf")
            with self.client.post(
                "/api/auth/login/", name="setup/login", timeout=timeout, allow_redirects=False,
                json={"username": self.account["username"], "password": os.environ["ORDER_LOAD_PASSWORD"]},
                headers={"X-CSRFToken": self.client.cookies["csrftoken"]}, catch_response=True,
            ) as response:
                if (response.status_code != 200 or not self.client.cookies.get("sessionid")
                        or response.json().get("username") != self.account["username"]
                        or response.json().get("id") != self.account["user_id"]):
                    response.failure("login_identity_failed")
                    raise ValueError("login")
        except Exception:
            STATE["fatal"] = "authentication_failed"
            raise StopUser()
        STATE["ready"] += 1

    @task
    def orders(self):
        state = STATE
        now = time.monotonic()
        if state["phase"] == "drain" or (state["measure_end"] and now >= state["measure_end"]):
            raise StopUser()
        measured = state["phase"] == "measure"
        if measured:
            state["inflight"] += 1
        status, ok, elapsed = 0, False, 0.0
        try:
            with self.client.get(
                f"/api/orders/?page={state['plan']['page']}", name=f"orders/page-{state['plan']['page']}",
                timeout=state["plan"]["request_timeout_seconds"], allow_redirects=False,
                catch_response=True,
            ) as response:
                status = response.status_code
                elapsed = response.request_meta["response_time"]
                try:
                    if status != 200:
                        raise ValueError("http_status")
                    validate_response(response.json(), self.account, state["plan"]["page"])
                    ok = True
                except (ValueError, KeyError, TypeError):
                    response.failure("invalid_order_page")
                    if status == 200:
                        state["fatal"] = "response_correctness_failed"
        finally:
            if measured:
                row = {"elapsed_ms": elapsed, "ok": ok, "status": status,
                       "finished_in_window": time.monotonic() <= state["measure_end"]}
                state["samples"].append(row)
                state["recent"].append(row)
                if not state["raw"].closed:
                    state["raw"].write(json.dumps(row) + "\n")
                state["inflight"] -= 1

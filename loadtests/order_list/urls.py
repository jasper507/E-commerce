"""Target identity and a small HTTP allowlist exist only in the load image."""

import hmac
import json
import os
from pathlib import Path

from django.db import connection
from django.http import JsonResponse
from django.urls import include, path

from loadtests.order_list.core import DATABASE, DB_USER


def require_database():
    config = connection.settings_dict
    if (connection.vendor != "mysql" or config["NAME"] != DATABASE
            or config["USER"] != DB_USER or config["HOST"] != "db"
            or str(config["PORT"]) != "3306"):
        raise RuntimeError("Refusing a non-load-test database configuration.")
    with connection.cursor() as cursor:
        cursor.execute("SELECT DATABASE(), CURRENT_USER()")
        name, identity = cursor.fetchone()
    if name != DATABASE or identity.split("@", 1)[0] != DB_USER:
        raise RuntimeError("Actual database identity does not match the dedicated target.")


class LoadTargetGuard:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == "/health/" and request.method == "GET":
            return self.get_response(request)
        allowed = {
            ("GET", "/__order_load__/ready/"), ("GET", "/api/auth/csrf/"),
            ("POST", "/api/auth/login/"), ("GET", "/api/auth/me/"),
            ("GET", "/api/orders/"),
        }
        token = request.headers.get("X-Order-Load-Token", "")
        if ((request.method, request.path) not in allowed
                or not hmac.compare_digest(token, os.environ["ORDER_LOAD_TOKEN"])):
            return JsonResponse({"error": "load_target_only"}, status=403)
        return self.get_response(request)


def ready(request):
    require_database()
    manifest = Path("/artifacts/dataset.json")
    if not manifest.exists():
        return JsonResponse({"error": "dataset_not_ready"}, status=503)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if data["run_id"] != os.environ["ORDER_LOAD_RUN"] or data["database"] != DATABASE:
        return JsonResponse({"error": "dataset_identity_mismatch"}, status=503)
    return JsonResponse({
        "suite": "project-one-order-load-v1", "run_id": data["run_id"],
        "database": DATABASE, "total_orders": data["total_orders"],
        "accounts": len(data["accounts"]), "page": data["page"],
        "index_mode": data["index_mode"],
    })


urlpatterns = [path("__order_load__/ready/", ready), path("", include("config.urls"))]

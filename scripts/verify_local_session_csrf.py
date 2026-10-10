"""Exercise only the approved independent local deployment; never print secrets."""

import argparse
import http.cookiejar
import json
import secrets
import subprocess
import urllib.error
import urllib.request
from http.cookies import SimpleCookie
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "http://127.0.0.1:8080"
COMPOSE = [
    "docker", "compose", "--env-file", "docker/deployment/.env",
    "-f", "compose.deployment-local.yaml", "-p", "project-one-deployment-local",
]

# Credentials and exact Session keys enter the container over stdin, not argv.
DATABASE_ACTION = r'''
import json, os, re, sys
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.db import connection, transaction
from django.db.models.deletion import Collector
from apps.catalog.models import Product
from apps.ordering.models import Order

p = json.load(sys.stdin)
with connection.cursor() as cursor:
    cursor.execute("SELECT DATABASE(), CURRENT_USER()")
    database, database_user = cursor.fetchone()
if (database != "project_one_deployment_local"
    or database_user != "project_one_deployment@%"
    or settings.DATABASES["default"]["HOST"] != "db"
    or str(settings.DATABASES["default"]["PORT"]) != "3306"
    or settings.ENVIRONMENT != "development" or settings.DEBUG
    or os.getuid() != 10001):
    raise RuntimeError("Deployment source guard refused")
User = get_user_model()
def counts():
    return dict(users=User.objects.count(), sessions=Session.objects.count(),
                products=Product.objects.count(), orders=Order.objects.count())

action = p["action"]
result = {"database": database, "row_counts": counts()}
if action == "preflight":
    if any(result["row_counts"].values()):
        raise RuntimeError("Expected empty deployment business data; refused")
elif action == "create":
    if any(result["row_counts"].values()):
        raise RuntimeError("Deployment changed since preflight; refused")
    if not re.fullmatch(r"deploy9c_[a-f0-9]{12}", p["username"]):
        raise RuntimeError("Unexpected verification username")
    with transaction.atomic():
        user = User.objects.create_user(username=p["username"], password=p["password"])
        if user.is_staff or user.is_superuser:
            raise RuntimeError("Unexpected elevated verification account")
    result.update(user_id=user.pk, row_counts=counts())
else:
    user = User.objects.get(pk=p["user_id"], username=p["username"])
    if user.is_staff or user.is_superuser:
        raise RuntimeError("Verification account changed; refused")
    sessions = list(Session.objects.filter(session_key__in=p.get("session_keys", [])))
    if any(s.get_decoded().get("_auth_user_id") != str(user.pk) for s in sessions):
        raise RuntimeError("Session ownership mismatch; refused")
    if action == "inspect":
        result.update(owned_sessions=len(sessions), last_login_set=user.last_login is not None)
    elif action == "cleanup":
        # Refuse deletion if another flow attached business data to this account.
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=user.pk, username=p["username"])
            if user.is_staff or user.is_superuser:
                raise RuntimeError("Verification account changed; cleanup refused")
            collector = Collector(using="default")
            collector.collect([user])
            if any(model is not User and rows for model, rows in collector.data.items()):
                raise RuntimeError("Unexpected related data; cleanup refused")
            if any(queryset.exists() for queryset in collector.fast_deletes):
                raise RuntimeError("Unexpected related data; cleanup refused")
            for session in sessions:
                Session.objects.filter(pk=session.pk, session_data=session.session_data).delete()
            user.delete()
        result.update(account_deleted=True, residual_owned_sessions_deleted=len(sessions), row_counts=counts())
    else:
        raise RuntimeError("Unknown verification action")
print(json.dumps(result))
'''


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def database_action(action, **payload):
    result = subprocess.run(
        COMPOSE + ["exec", "-T", "web", "python", "-c", DATABASE_ACTION],
        cwd=ROOT, input=json.dumps({"action": action, **payload}),
        text=True, encoding="utf-8", capture_output=True, timeout=30,
    )
    # Do not expose raw process errors or input if a guard/connection fails.
    require(result.returncode == 0, f"Database action {action} refused or failed")
    return json.loads(result.stdout)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true", help="Read-only source/count check")
    mode.add_argument("--execute-approved", action="store_true", help="Requires prior owner data approval")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report_path = args.report.resolve()
    require(not report_path.exists(), "Report already exists; do not overwrite evidence")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {"origin": ORIGIN, "browser_ui_verified": False, "checks": []}
    preflight = database_action("preflight")
    report["preflight"] = preflight
    if args.preflight:
        report.update(status="preflight_passed", database_writes=False)
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report))
        return

    username = "deploy9c_" + secrets.token_hex(6)
    password = secrets.token_urlsafe(24)
    report.update(status="running", run_username=username, database_writes=True)
    jar = http.cookiejar.CookieJar()
    client = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), NoRedirect(),
        urllib.request.HTTPCookieProcessor(jar),
    )
    session_keys = set()
    user_id = None
    create_attempted = False

    def save():
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    def cookie(name):
        return next((c.value for c in jar if c.name == name), None)

    def request(label, path, expected, *, payload=None, token=None, origin=ORIGIN):
        headers = {"Accept": "application/json"}
        data = None
        method = "GET"
        if payload is not None:
            data = json.dumps(payload).encode()
            method = "POST"
            headers.update({"Content-Type": "application/json", "Origin": origin})
            if token is not None:
                headers["X-CSRFToken"] = token
        req = urllib.request.Request(ORIGIN + path, data=data, headers=headers, method=method)
        try:
            response = client.open(req, timeout=10)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            status = response.code
            body = response.read()
            set_cookies = response.headers.get_all("Set-Cookie", [])
        current_key = cookie("sessionid")
        if current_key:
            session_keys.add(current_key)
        report["checks"].append({"label": label, "method": method, "path": path, "status": status, "expected": expected})
        save()
        require(status == expected, f"Unexpected HTTP status for {label}")
        parsed = json.loads(body) if body and path.startswith("/api/") else None
        return parsed, set_cookies

    def inspect(expected_sessions):
        state = database_action("inspect", username=username, user_id=user_id, session_keys=list(session_keys))
        require(state["row_counts"]["users"] == 1, "Unexpected account count")
        require(state["row_counts"]["sessions"] == expected_sessions, "Unexpected Session count")
        require(state["owned_sessions"] == expected_sessions, "Session ownership/count differs")
        return state

    def csrf_rejected(parsed):
        require(parsed == {"detail": "CSRF verification failed."}, "Not the expected CSRF rejection")

    def same_user(parsed):
        require(parsed == {"id": user_id, "username": username, "is_staff": False}, "Unexpected authenticated identity")

    save()
    try:
        create_attempted = True
        report["create_attempted"] = True
        save()
        created = database_action("create", username=username, password=password)
        user_id = created["user_id"]
        report["user_id"] = user_id
        save()
        request("anonymous me", "/api/auth/me/", 403)
        _, csrf_headers = request("fetch csrf", "/api/auth/csrf/", 204)
        old_token = cookie("csrftoken")
        require(bool(old_token) and cookie("sessionid") is None, "Unexpected pre-login cookies")
        inspect(0)
        login_payload = {"username": username, "password": password}
        parsed, _ = request("login without csrf", "/api/auth/login/", 403, payload=login_payload)
        csrf_rejected(parsed)
        inspect(0)
        parsed, login_headers = request("same-origin login", "/api/auth/login/", 200, payload=login_payload, token=old_token)
        same_user(parsed)
        new_token = cookie("csrftoken")
        require(bool(new_token) and new_token != old_token, "CSRF was not rotated by login")
        require(bool(cookie("sessionid")), "Session cookie missing")
        attributes = {}
        for raw in csrf_headers + login_headers:
            cookies = SimpleCookie()
            cookies.load(raw)
            for name, morsel in cookies.items():
                if name in {"sessionid", "csrftoken"}:
                    attributes[name] = {"httponly": bool(morsel["httponly"]), "samesite": morsel["samesite"], "secure": bool(morsel["secure"])}
        require(attributes["sessionid"] == {"httponly": True, "samesite": "Lax", "secure": False}, "Unexpected Session cookie attributes")
        require(attributes["csrftoken"] == {"httponly": False, "samesite": "Lax", "secure": False}, "Unexpected CSRF cookie attributes")
        report.update(cookie_attributes=attributes, login_rotated_csrf=True, login_state=inspect(1))
        require(report["login_state"]["last_login_set"], "Login timestamp missing")
        parsed, _ = request("authenticated me", "/api/auth/me/", 200)
        same_user(parsed)
        request("navigate public page", "/products", 200)
        parsed, _ = request("me after navigation", "/api/auth/me/", 200)
        same_user(parsed)
        for label, token, origin in [
            ("logout missing csrf", None, ORIGIN),
            ("logout old csrf header", old_token, ORIGIN),
            ("logout wrong origin", new_token, "http://localhost:8080"),
        ]:
            parsed, _ = request(label, "/api/auth/logout/", 403, payload={}, token=token, origin=origin)
            csrf_rejected(parsed)
            parsed, _ = request(label + " preserves me", "/api/auth/me/", 200)
            same_user(parsed)
            inspect(1)
        request("valid logout", "/api/auth/logout/", 204, payload={}, token=new_token)
        require(cookie("sessionid") is None, "Logout did not remove client Session cookie")
        request("me after logout", "/api/auth/me/", 403)
        report["logout_state"] = inspect(0)
        report["status"] = "passed"
    except Exception as exc:
        report.update(status="failed", failure_type=type(exc).__name__)
        raise
    finally:
        if user_id is not None:
            try:
                report["cleanup"] = database_action("cleanup", username=username, user_id=user_id, session_keys=list(session_keys))
                require(all(v == 0 for v in report["cleanup"]["row_counts"].values()), "Unexpected rows after scoped cleanup")
            except Exception:
                report["cleanup_status"] = "refused_or_failed_preserve_for_review"
                report["status"] = "failed"
                save()
                raise
        elif create_attempted:
            # A lost container response cannot establish whether create committed.
            report["cleanup_status"] = "account_creation_outcome_unknown_preserve_for_review"
            report["status"] = "failed"
        report["secrets_or_session_keys_recorded"] = False
        save()
    print(json.dumps({"status": report["status"], "http_checks": len(report["checks"]), "scoped_cleanup_complete": report["cleanup"]["account_deleted"], "report": str(report_path)}))


if __name__ == "__main__":
    main()

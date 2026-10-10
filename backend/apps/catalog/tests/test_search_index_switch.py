"""手动重建与别名切换的真实隔离证据；仅清理本次构建器拥有的索引。"""

import json
import os
from pathlib import Path
from queue import Queue
from threading import Event, Thread, current_thread
from uuid import uuid4

import pytest
from django.conf import settings
from django.db import close_old_connections, connection
from django.urls import reverse
from elasticsearch import Elasticsearch, NotFoundError
from elasticsearch.helpers import BulkIndexError
from elastic_transport import ConnectionTimeout
from rest_framework.test import APIClient

from apps.catalog import search_prototype, search_read
from apps.catalog.models import Category, Product
from apps.catalog.search_alias import (
    READ_ALIAS_PREFIX, SearchAliasConflict, SearchAliasInvalid, SearchIndexPublisher,
)
from apps.catalog.search_prototype import SearchIndexPrototype


TEST_URL = os.getenv("PROJECT_ONE_TEST_ELASTICSEARCH_URL")
ENABLED = bool(TEST_URL) and os.getenv("PROJECT_ONE_TEST_ES_OFFLINE") != "1"
TEST_DATABASE = "test_project_one"
pytestmark = [
    pytest.mark.elasticsearch_integration,
    pytest.mark.skipif(not ENABLED, reason="explicit isolated Elasticsearch URL required"),
]

if ENABLED and (
    settings.ENVIRONMENT != "test"
    or settings.DATABASES["default"]["TEST"]["NAME"] != TEST_DATABASE
    or settings.DATABASES["default"]["NAME"] == TEST_DATABASE
):
    raise RuntimeError("Index switch evidence requires DJANGO_ENV=test and isolated test_project_one.")


@pytest.fixture(scope="session")
def evidence(request):
    path_text = os.getenv("PROJECT_ONE_SEARCH_SWITCH_REPORT")
    path = Path(path_text) if path_text else None
    if path:
        assert path.is_absolute() and path.parent.is_dir()
        assert not path.exists(), "Refuse to overwrite previous evidence."
    report = {"date": "2026-10-10", "observations": [], "cleanup": []}
    yield report
    report["outcome"] = "failed" if request.session.testsfailed or report.get("cleanup_failed") else "passed"
    report["boundary"] = (
        "Real isolated ES alias operations and MySQL/APIClient; response-loss/readback-loss "
        "are injected after real operations. One controlled reader and one publisher, "
        "not distributed publication coordination, production performance or automatic synchronization."
    )
    if path:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")


def record(evidence, scenario, data):
    observation = {"scenario": scenario, **data}
    evidence["observations"].append(observation)
    print("search_index_switch " + json.dumps(observation, ensure_ascii=False))


def assert_actual_test_database():
    assert connection.vendor == "mysql" and connection.settings_dict["NAME"] == TEST_DATABASE
    with connection.cursor() as cursor:
        cursor.execute("SELECT DATABASE()")
        assert cursor.fetchone()[0] == TEST_DATABASE
    assert connection.get_autocommit() and not connection.in_atomic_block


@pytest.fixture
def guarded_db(transactional_db):
    assert_actual_test_database()
    try:
        yield
    finally:
        assert_actual_test_database()


@pytest.fixture
def switch_session(guarded_db, settings, evidence):
    category = Category.objects.create(name="手动切换", slug="switch-catalog", is_active=True)
    changing = Product.objects.create(
        category=category, slug="changing-product", name="旧款键盘", description="耐用外壳", is_active=True,
    )
    stable = Product.objects.create(
        category=category, slug="stable-product", name="稳定鼠标", description="常用配件", is_active=True,
    )
    prototype = SearchIndexPrototype(TEST_URL)
    alias = READ_ALIAS_PREFIX + uuid4().hex
    publisher = SearchIndexPublisher(prototype, alias)
    workers = []
    info = prototype.client.info()
    evidence["elasticsearch"] = {"cluster": info["cluster_name"], "version": info["version"]["number"]}
    try:
        old = prototype.rebuild()
        assert old["documents"] == 2
        bootstrap = publisher.publish(old["index"], expected_index=None)
        assert bootstrap["state"] == "switched" and bootstrap["current_index"] == old["index"]
        settings.CATALOG_SEARCH_ENABLED = True
        settings.CATALOG_SEARCH_URL = TEST_URL
        settings.CATALOG_SEARCH_INDEX = ""
        settings.CATALOG_SEARCH_ALIAS = alias
        yield {
            "prototype": prototype, "publisher": publisher, "alias": alias,
            "old": old, "category": category, "changing": changing, "stable": stable,
            "workers": workers,
        }
    finally:
        owned = sorted(prototype._indices)
        try:
            assert not any(worker.is_alive() for worker in workers), "Refuse index cleanup while a reader is active."
            assert_actual_test_database()
            prototype.close()
            with Elasticsearch(TEST_URL, request_timeout=2, max_retries=0) as control:
                remaining = [index for index in owned if control.indices.exists(index=index)]
                with pytest.raises(NotFoundError):
                    control.indices.get_alias(name=alias)
            evidence["cleanup"].append({"owned_indices": owned, "remaining": remaining, "alias": alias, "alias_missing": True})
            assert not remaining
            assert_actual_test_database()
        except Exception:
            evidence["cleanup_failed"] = True
            raise


def update_source(data):
    assert_actual_test_database()
    Product.objects.filter(pk=data["changing"].pk).update(name="新款音箱")
    added = Product.objects.create(
        category=data["category"], slug="added-product", name="新款音箱", description="耐用外壳", is_active=True,
    )
    return added


def actual_alias(alias):
    # This control client is independent from publication wrappers and the API reader.
    with Elasticsearch(TEST_URL, request_timeout=2, max_retries=0) as control:
        result = dict(control.indices.get_alias(name=alias))
    assert len(result) == 1
    return next(iter(result)), result


def request_search(keyword):
    return APIClient(enforce_csrf_checks=True).get(reverse("catalog:product-search"), {"q": keyword})


def summary(slug, name):
    return {
        "slug": slug, "name": name, "category": {"slug": "switch-catalog", "name": "手动切换"},
        "lowest_price": None, "in_stock": False,
    }


def success_payload(response):
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"candidate_count", "next_candidate_page", "previous_candidate_page", "results"}
    assert payload["next_candidate_page"] is None and payload["previous_candidate_page"] is None
    return payload


def assert_new_keyword(response):
    payload = success_payload(response)
    assert payload["candidate_count"] == 2
    assert payload["results"] == [summary("changing-product", "新款音箱"), summary("added-product", "新款音箱")]
    return payload


def test_successful_rebuild_switches_new_requests_and_keeps_old_exact_index(switch_session, settings, evidence):
    data = switch_session
    old_index = data["old"]["index"]
    added = update_source(data)
    before = success_payload(request_search("新款音箱"))
    assert before["candidate_count"] == 0 and before["results"] == []
    switched = data["publisher"].rebuild_and_publish(expected_index=old_index)
    new_index = switched["candidate"]
    assert new_index != old_index
    assert switched["state"] == "switched" and switched["current_index"] == new_index
    target, alias_response = actual_alias(data["alias"])
    assert target == new_index
    after = assert_new_keyword(request_search("新款音箱"))
    old_candidates = data["prototype"].search(old_index, q="旧款键盘")
    assert [hit["_source"]["slug"] for hit in old_candidates["hits"]["hits"]] == ["changing-product"]
    assert old_candidates["hits"]["hits"][0]["_source"]["name"] == "旧款键盘"
    assert data["prototype"].client.count(index=old_index)["count"] == 2
    assert data["prototype"].client.count(index=new_index)["count"] == 3
    settings.CATALOG_SEARCH_ALIAS = ""
    settings.CATALOG_SEARCH_INDEX = old_index
    old_exact = success_payload(request_search("旧款键盘"))
    assert old_exact["candidate_count"] == 1 and old_exact["results"] == [summary("changing-product", "新款音箱")]
    record(evidence, "successful_rebuild_and_old_index_retention", {
        "old": data["old"], "switch": switched, "independent_alias": alias_response,
        "new_keyword_before": before, "new_keyword_after": after, "old_exact_candidates": old_candidates,
        "old_exact_api_response": old_exact, "oracle_new_slugs": ["changing-product", "added-product"],
        "added_product_id": added.pk,
    })


def test_real_bulk_failure_leaves_alias_and_old_read_available(switch_session, monkeypatch, evidence):
    data = switch_session
    prototype = data["prototype"]
    old_index = data["old"]["index"]
    documents = search_prototype.product_documents()
    invalid = {**documents[0], "product_id": "invalid", "slug": "invalid-document"}
    published = []
    original_update = prototype.client.indices.update_aliases

    def observe_update(**kwargs):
        published.append(kwargs)
        return original_update(**kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(prototype.client.indices, "update_aliases", observe_update)
        patch.setattr(search_prototype, "product_documents", lambda: [documents[0], invalid])
        with pytest.raises(BulkIndexError) as error:
            data["publisher"].rebuild_and_publish(expected_index=old_index)
    failed = prototype._indices - {old_index}
    assert len(failed) == 1 and published == [] and prototype._ready == {old_index}
    failed_index = next(iter(failed))
    prototype.client.indices.refresh(index=failed_index)
    assert prototype.client.count(index=failed_index)["count"] == 1
    assert prototype.client.indices.get_mapping(index=failed_index)[failed_index]["mappings"]["_meta"]["build_complete"] is False
    assert error.value.errors[0]["index"]["status"] == 400
    with pytest.raises(SearchAliasInvalid, match="completed candidate"):
        data["publisher"].publish(failed_index, expected_index=old_index)
    target, alias_response = actual_alias(data["alias"])
    assert target == old_index
    old_response = success_payload(request_search("旧款键盘"))
    assert old_response["candidate_count"] == 1 and old_response["results"] == [summary("changing-product", "旧款键盘")]
    assert prototype.client.count(index=old_index)["count"] == 2
    record(evidence, "real_bulk_failure_keeps_old_pointer", {
        "failed_candidate": failed_index, "failed_candidate_documents": 1,
        "bulk_errors": error.value.errors, "alias_update_calls": len(published),
        "independent_alias": alias_response, "old_api_response": old_response,
    })


def test_inflight_reader_uses_resolved_old_index_then_later_request_uses_new(switch_session, monkeypatch, evidence):
    data = switch_session
    old_index = data["old"]["index"]
    update_source(data)
    new = data["prototype"].rebuild()
    resolved = Event()
    release = Event()
    results = Queue()
    events = []
    worker_name = "search-old-resolved-reader"
    original_resolve = search_read.resolve_read_alias
    original_read = search_read.SearchIndexReader._read
    observed_indices = []

    def pause_after_resolve(client, alias, **kwargs):
        index = original_resolve(client, alias, **kwargs)
        if current_thread().name == worker_name:
            assert index == old_index
            events.append("resolved_old")
            resolved.set()
            assert release.wait(5), "Reader was not released after the controlled alias switch."
        return index

    def observe_exact_read(self, client, index, *args, **kwargs):
        if current_thread().name == worker_name:
            observed_indices.append(index)
            events.append("searched_old_exact")
        return original_read(self, client, index, *args, **kwargs)

    def worker():
        close_old_connections()
        try:
            assert_actual_test_database()
            response = request_search("旧款键盘")
            assert_actual_test_database()
            results.put({"status": response.status_code, "response": response.json()})
            events.append("old_response_complete")
        except BaseException as error:
            results.put(error)
        finally:
            connection.close()

    thread = Thread(target=worker, name=worker_name, daemon=True)
    data["workers"].append(thread)
    with monkeypatch.context() as patch:
        patch.setattr(search_read, "resolve_read_alias", pause_after_resolve)
        patch.setattr(search_read.SearchIndexReader, "_read", observe_exact_read)
        thread.start()
        try:
            assert resolved.wait(5), "Worker did not resolve the initial alias target."
            switched = data["publisher"].publish(new["index"], expected_index=old_index)
            assert switched["state"] == "switched"
            events.append("switched_new")
        finally:
            release.set()
            thread.join(5)
            assert not thread.is_alive(), "Do not clean indices while the controlled reader is still active."
    completed = results.get_nowait()
    if isinstance(completed, BaseException):
        raise completed
    assert completed == {"status": 200, "response": {
        "candidate_count": 1, "next_candidate_page": None, "previous_candidate_page": None,
        "results": [summary("changing-product", "新款音箱")],
    }}
    assert observed_indices == [old_index]
    assert events == ["resolved_old", "switched_new", "searched_old_exact", "old_response_complete"]
    later = assert_new_keyword(request_search("新款音箱"))
    old_keyword_after = success_payload(request_search("旧款键盘"))
    assert old_keyword_after["candidate_count"] == 0 and old_keyword_after["results"] == []
    assert data["prototype"].client.indices.exists(index=old_index)
    record(evidence, "inflight_resolved_old_index", {
        "switch": switched, "events": events, "resolved_search_indices": observed_indices,
        "inflight_old_response": completed, "later_new_response": later,
        "later_old_keyword_response": old_keyword_after,
    })


def test_real_alias_commit_response_loss_and_readback_loss_are_distinguished(switch_session, monkeypatch, evidence):
    data = switch_session
    prototype = data["prototype"]
    update_source(data)
    old_index = data["old"]["index"]
    first_candidate = prototype.rebuild()["index"]
    original_update = prototype.client.indices.update_aliases
    original_get = prototype.client.indices.get_alias
    observations = []

    for label, candidate, expected, fail_readback in (
        ("response_lost_readback_succeeds", first_candidate, old_index, False),
        ("response_and_readback_lost", None, first_candidate, True),
    ):
        candidate = candidate or prototype.rebuild()["index"]
        calls = []
        state = {"post_executed": False, "actual_response": None, "readback_failures": 0}

        def real_update_then_timeout(**kwargs):
            calls.append(kwargs["actions"])
            state["actual_response"] = dict(original_update(**kwargs))
            state["post_executed"] = True
            raise ConnectionTimeout("Injected response loss after the real alias operation.")

        def conditional_get(**kwargs):
            if fail_readback and state["post_executed"]:
                state["readback_failures"] += 1
                raise ConnectionTimeout("Injected readback loss after the committed alias operation.")
            return original_get(**kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(prototype.client.indices, "update_aliases", real_update_then_timeout)
            patch.setattr(prototype.client.indices, "get_alias", conditional_get)
            outcome = data["publisher"].publish(candidate, expected_index=expected)
        assert calls == [[
            {"remove": {"index": expected, "alias": data["alias"], "must_exist": True}},
            {"add": {"index": candidate, "alias": data["alias"]}},
        ]]
        assert state["post_executed"] and outcome["response"] is None
        assert outcome["request_error"] == "ConnectionTimeout"
        target, alias_response = actual_alias(data["alias"])
        assert target == candidate
        if fail_readback:
            assert outcome["state"] == "unknown" and outcome["current_index"] is None
            assert outcome["readback_error"] == "ConnectionTimeout" and state["readback_failures"] == 1
        else:
            assert outcome["state"] == "switched" and outcome["current_index"] == candidate
            assert outcome["readback_error"] is None and state["readback_failures"] == 0
        for retained in (old_index, first_candidate, candidate):
            assert prototype.client.indices.exists(index=retained)
        observations.append({
            "injection": label, "publisher_outcome": outcome, "actual_post_response": state["actual_response"],
            "alias_update_calls": len(calls), "readback_failures": state["readback_failures"],
            "independent_alias": alias_response,
        })
    final_api = assert_new_keyword(request_search("新款音箱"))
    record(evidence, "real_alias_post_then_response_loss", {
        "attempts": observations, "final_api_response": final_api, "all_candidates_retained": sorted(prototype._indices),
    })


def test_unfinished_candidate_stale_expected_and_abnormal_alias_are_rejected(switch_session, evidence):
    data = switch_session
    prototype = data["prototype"]
    old_index = data["old"]["index"]
    new_index = prototype.rebuild()["index"]
    good_meta = {"search_schema": 1, "source_database": TEST_DATABASE, "build_complete": True}
    prototype.client.indices.put_mapping(index=new_index, _meta={**good_meta, "build_complete": False})
    with pytest.raises(SearchAliasInvalid, match="unfinished"):
        data["publisher"].publish(new_index, expected_index=old_index)
    assert actual_alias(data["alias"])[0] == old_index
    prototype.client.indices.put_mapping(index=new_index, _meta=good_meta)
    switched = data["publisher"].publish(new_index, expected_index=old_index)
    assert switched["state"] == "switched"
    with pytest.raises(SearchAliasConflict, match="changed"):
        data["publisher"].publish(old_index, expected_index=old_index)
    assert actual_alias(data["alias"])[0] == new_index

    rejected = []
    prototype.client.indices.update_aliases(actions=[{"add": {"index": old_index, "alias": data["alias"]}}])
    multiple = dict(prototype.client.indices.get_alias(name=data["alias"]))
    assert set(multiple) == {old_index, new_index}
    response = request_search("旧款键盘")
    assert response.status_code == 503
    with pytest.raises(SearchAliasInvalid, match="exactly one"):
        data["publisher"].publish(old_index, expected_index=new_index)
    rejected.append({"pointer": "multiple_targets", "actual_alias": multiple, "api_status": response.status_code})
    prototype.client.indices.update_aliases(actions=[
        {"remove": {"index": old_index, "alias": data["alias"], "must_exist": True}},
    ])
    for option, extra in (
        ("filtered_alias", {"filter": {"term": {"product_id": data["changing"].pk}}}),
        ("routed_alias", {"routing": "isolated-routing"}),
    ):
        prototype.client.indices.update_aliases(actions=[
            {"remove": {"index": new_index, "alias": data["alias"], "must_exist": True}},
            {"add": {"index": new_index, "alias": data["alias"], **extra}},
        ])
        actual = dict(prototype.client.indices.get_alias(name=data["alias"]))
        response = request_search("旧款键盘")
        assert response.status_code == 503
        rejected.append({"pointer": option, "actual_alias": actual, "api_status": response.status_code})
    prototype.client.indices.update_aliases(actions=[
        {"remove": {"index": new_index, "alias": data["alias"], "must_exist": True}},
        {"add": {"index": new_index, "alias": data["alias"]}},
    ])
    restored = success_payload(request_search("旧款键盘"))
    assert restored["candidate_count"] == 1 and restored["results"] == [summary("changing-product", "旧款键盘")]
    record(evidence, "unfinished_candidate_and_invalid_pointer", {
        "incomplete_publish_rejected": True, "stale_expected_rejected": True,
        "abnormal_alias_rejections": rejected, "restored_api_response": restored,
    })

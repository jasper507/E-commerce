"""ES 候选页回读当前 MySQL 摘要；仅写实际隔离库和自己创建的索引。"""

import json
import os
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.conf import settings
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from elasticsearch import ApiError, Elasticsearch
from elastic_transport import TransportError
from rest_framework.test import APIClient

from apps.catalog import search_read, views
from apps.catalog.models import Category, Product, SKU
from apps.catalog.search_prototype import CLUSTER_NAME, INDEX_PREFIX, SearchIndexPrototype
from apps.inventory.models import Inventory


TEST_URL = os.getenv("PROJECT_ONE_TEST_ELASTICSEARCH_URL")
OFFLINE = os.getenv("PROJECT_ONE_TEST_ES_OFFLINE") == "1"
ONLINE = bool(TEST_URL) and not OFFLINE
TEST_DATABASE = "test_project_one"
pytestmark = pytest.mark.elasticsearch_integration
online_only = pytest.mark.skipif(not ONLINE, reason="explicit isolated Elasticsearch URL required")

if (ONLINE or OFFLINE) and (
    settings.ENVIRONMENT != "test"
    or settings.DATABASES["default"]["TEST"]["NAME"] != TEST_DATABASE
    or settings.DATABASES["default"]["NAME"] == TEST_DATABASE
):
    raise RuntimeError("Search read evidence requires DJANGO_ENV=test and isolated test_project_one.")


@pytest.fixture(scope="session")
def evidence(request):
    path_text = os.getenv("PROJECT_ONE_SEARCH_READ_REPORT")
    path = Path(path_text) if path_text else None
    if path:
        assert path.is_absolute() and path.parent.is_dir()
        assert not path.exists(), "Refuse to overwrite previous evidence."
    report = {"date": "2026-10-10", "observations": [], "cleanup": []}
    yield report
    report["outcome"] = "failed" if request.session.testsfailed or report.get("cleanup_failed") else "passed"
    report["boundary"] = (
        "Isolated APIClient and real services for designated integration scenarios; injected "
        "reader/client/connection cases cover the candidate window and malformed response guards. "
        "No browser, automatic synchronization, or capacity evidence."
    )
    if path:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")


def record(evidence, scenario, data):
    observation = {"scenario": scenario, **data}
    evidence["observations"].append(observation)
    print("search_read_api " + json.dumps(observation, ensure_ascii=False))


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
        # Guard teardown as well; never mutate a real connection's target to test rejection.
        assert_actual_test_database()


@pytest.fixture
def prototype(guarded_db, evidence):
    instance = SearchIndexPrototype(TEST_URL)
    info = instance.client.info()
    evidence["elasticsearch"] = {"cluster": info["cluster_name"], "version": info["version"]["number"]}
    try:
        yield instance
    finally:
        owned = sorted(instance._indices)
        try:
            assert_actual_test_database()
            instance.close()
            with Elasticsearch(TEST_URL, request_timeout=2, max_retries=0) as control:
                remaining = [index for index in owned if control.indices.exists(index=index)]
            evidence["cleanup"].append({"owned_indices": owned, "remaining": remaining})
            assert not remaining
            assert_actual_test_database()
        except Exception:
            evidence["cleanup_failed"] = True
            raise


@pytest.fixture
def catalog(guarded_db):
    selected = Category.objects.create(name="回读鼠标", slug="read-mice", is_active=True)
    other = Category.objects.create(name="回读配件", slug="read-accessories", is_active=True)
    later_hidden = Category.objects.create(name="随后隐藏", slug="read-later-hidden", is_active=True)
    hidden = Category.objects.create(name="已隐藏分类", slug="read-hidden", is_active=False)
    products = {}
    specifications = (
        (selected, "description-hit", "轻巧便携", "无线鼠标", True),
        (selected, "name-hit", "无线鼠标", "轻巧便携", True),
        (selected, "stale-product", "陈旧键盘", "原始描述", True),
        (later_hidden, "stale-category", "陈旧键盘", "原始描述", True),
        (selected, "moved-product", "陈旧键盘", "原始描述", True),
        (selected, "renamed-product", "陈旧键盘", "原始描述", True),
        (selected, "no-sku", "陈旧键盘", "原始描述", True),
        (selected, "already-inactive", "陈旧键盘", "原始描述", False),
        (hidden, "already-hidden", "陈旧键盘", "原始描述", True),
    )
    for category, slug, name, description, active in specifications:
        products[slug] = Product.objects.create(
            category=category, slug=slug, name=name, description=description, is_active=active,
        )
    active_sku = SKU.objects.create(
        product=products["name-hit"], code="READ-NAME-A", price=Decimal("129.00"), is_active=True,
    )
    Inventory.objects.create(sku=active_sku, available_stock=0)
    second_sku = SKU.objects.create(
        product=products["name-hit"], code="READ-NAME-B", price=Decimal("149.00"), is_active=True,
    )
    Inventory.objects.create(sku=second_sku, available_stock=5)
    hidden_sku = SKU.objects.create(
        product=products["name-hit"], code="READ-NAME-H", price=Decimal("1.00"), is_active=False,
    )
    Inventory.objects.create(sku=hidden_sku, available_stock=99)
    renamed_sku = SKU.objects.create(
        product=products["renamed-product"], code="READ-RENAMED", price=Decimal("159.00"), is_active=True,
    )
    renamed_inventory = Inventory.objects.create(sku=renamed_sku, available_stock=0)
    try:
        yield {
            "selected": selected, "other": other, "later_hidden": later_hidden,
            "products": products, "renamed_sku": renamed_sku, "renamed_inventory": renamed_inventory,
        }
    finally:
        assert_actual_test_database()


def enable_reader(settings, index):
    settings.CATALOG_SEARCH_ENABLED = True
    settings.CATALOG_SEARCH_URL = TEST_URL or "http://127.0.0.1:9201"
    settings.CATALOG_SEARCH_INDEX = index


def request_search(**params):
    return APIClient(enforce_csrf_checks=True).get(reverse("catalog:product-search"), params)


def payload(response):
    assert response.status_code == 200
    result = response.json()
    assert set(result) == {"candidate_count", "next_candidate_page", "previous_candidate_page", "results"}
    for product in result["results"]:
        assert set(product) == {"slug", "name", "category", "lowest_price", "in_stock"}
        assert set(product["category"]) == {"slug", "name"}
    return result


def public_summary(slug, name, category_slug="read-mice", category_name="回读鼠标", price=None, stock=False):
    return {
        "slug": slug, "name": name, "category": {"slug": category_slug, "name": category_name},
        "lowest_price": price, "in_stock": stock,
    }


@online_only
def test_real_candidate_order_and_current_public_summaries(prototype, catalog, settings, evidence):
    built = prototype.rebuild()
    assert built["documents"] == 7
    enable_reader(settings, built["index"])
    candidates = prototype.search(built["index"], q="无线鼠标")
    assert [hit["_source"]["slug"] for hit in candidates["hits"]["hits"]] == ["name-hit", "description-hit"]
    assert catalog["products"]["description-hit"].pk < catalog["products"]["name-hit"].pk
    with CaptureQueriesContext(connection) as queries:
        result = payload(request_search(q="  无线鼠标  "))
    search_sql = list(queries)
    assert result == {
        "candidate_count": 2, "next_candidate_page": None, "previous_candidate_page": None,
        "results": [
            public_summary("name-hit", "无线鼠标", price="129.00", stock=True),
            public_summary("description-hit", "轻巧便携"),
        ],
    }
    assert len(search_sql) == 2
    assert not connection.in_atomic_block
    reversed_terms = payload(request_search(q="鼠标 无线"))
    assert reversed_terms == result
    old_browse = APIClient().get(reverse("catalog:product-list"), {"q": "鼠标 无线"})
    assert old_browse.status_code == 200 and old_browse.json()["count"] == 0
    record(evidence, "candidate_order_and_summaries", {
        "build": built, "actual_candidates": candidates, "response": result,
        "request_sql_count": len(search_sql), "oracle_slugs": ["name-hit", "description-hit"],
        "reversed_terms_response": reversed_terms, "old_mysql_keyword_response": old_browse.json(),
    })


@online_only
def test_stale_candidates_filter_current_visibility_category_and_read_new_summary(prototype, catalog, settings, evidence):
    built = prototype.rebuild()
    enable_reader(settings, built["index"])
    original = prototype.search(built["index"], q="陈旧键盘")
    assert [hit["_source"]["slug"] for hit in original["hits"]["hits"]] == [
        "stale-product", "stale-category", "moved-product", "renamed-product", "no-sku",
    ]
    products = catalog["products"]
    Product.objects.filter(pk=products["stale-product"].pk).update(is_active=False)
    Category.objects.filter(pk=catalog["later_hidden"].pk).update(is_active=False)
    Product.objects.filter(pk=products["moved-product"].pk).update(category=catalog["other"])
    Product.objects.filter(pk=products["renamed-product"].pk).update(name="新款音箱")
    SKU.objects.filter(pk=catalog["renamed_sku"].pk).update(price=Decimal("89.00"))
    Inventory.objects.filter(pk=catalog["renamed_inventory"].pk).update(available_stock=7)
    Product.objects.create(
        category=catalog["selected"], slug="new-unindexed", name="全新麦克风", is_active=True,
    )

    current = payload(request_search(q="陈旧键盘"))
    assert current == {
        "candidate_count": 5, "next_candidate_page": None, "previous_candidate_page": None,
        "results": [
            public_summary("moved-product", "陈旧键盘", "read-accessories", "回读配件"),
            public_summary("renamed-product", "新款音箱", price="89.00", stock=True),
            public_summary("no-sku", "陈旧键盘"),
        ],
    }
    filtered = payload(request_search(q="陈旧键盘", category="read-mice"))
    assert filtered["candidate_count"] == 4
    assert filtered["results"] == [
        public_summary("renamed-product", "新款音箱", price="89.00", stock=True),
        public_summary("no-sku", "陈旧键盘"),
    ]
    renamed_query = payload(request_search(q="新款音箱"))
    moved_category = payload(request_search(q="陈旧键盘", category="read-accessories"))
    new_product = payload(request_search(q="全新麦克风"))
    assert renamed_query["candidate_count"] == moved_category["candidate_count"] == 0
    assert renamed_query["results"] == moved_category["results"] == []
    assert new_product["candidate_count"] == 0 and new_product["results"] == []
    record(evidence, "stale_candidates_and_current_mysql", {
        "original_candidates": original, "unfiltered_response": current, "selected_category_response": filtered,
        "new_keyword_response": renamed_query, "new_category_response": moved_category,
        "new_unindexed_product_response": new_product,
        "oracle_visible_slugs": ["moved-product", "renamed-product", "no-sku"],
    })


@online_only
def test_candidate_pages_remain_stable_when_visible_page_is_short_or_empty(prototype, guarded_db, settings, evidence):
    category = Category.objects.create(name="候选分页", slug="read-pages", is_active=True)
    products = [Product.objects.create(
        category=category, slug=f"candidate-{index:02d}", name="分页键盘", description="耐用外壳", is_active=True,
    ) for index in range(21)]
    built = prototype.rebuild()
    assert built["documents"] == 21
    enable_reader(settings, built["index"])
    first = payload(request_search(q="分页键盘", category="read-pages", page_size=100))
    assert first["candidate_count"] == 21 and len(first["results"]) == 20
    assert [item["slug"] for item in first["results"]] == [f"candidate-{index:02d}" for index in range(20)]
    assert first["next_candidate_page"] == 2 and first["previous_candidate_page"] is None
    Product.objects.filter(pk=products[0].pk).update(is_active=False)
    short = payload(request_search(q="分页键盘", category="read-pages"))
    assert short["candidate_count"] == 21 and short["next_candidate_page"] == 2
    assert [item["slug"] for item in short["results"]] == [f"candidate-{index:02d}" for index in range(1, 20)]
    Product.objects.filter(pk__in=[product.pk for product in products[:20]]).update(is_active=False)
    empty = payload(request_search(q="分页键盘", category="read-pages"))
    assert empty == {"candidate_count": 21, "next_candidate_page": 2, "previous_candidate_page": None, "results": []}
    second = payload(request_search(q="分页键盘", category="read-pages", page=2))
    assert second == {
        "candidate_count": 21, "next_candidate_page": None, "previous_candidate_page": 1,
        "results": [public_summary("candidate-20", "分页键盘", "read-pages", "候选分页")],
    }
    assert_actual_test_database()
    record(evidence, "short_and_empty_candidate_page", {
        "build": built, "original_page": first, "short_page": short, "empty_page": empty, "second_page": second,
        "oracle_second_page_slug": "candidate-20",
    })


@online_only
def test_zero_candidates_first_page_is_empty_and_tail_pages_are_not_found(prototype, catalog, settings, evidence):
    built = prototype.rebuild()
    enable_reader(settings, built["index"])
    empty = payload(request_search(q="绝不匹配测试词"))
    assert empty == {"candidate_count": 0, "next_candidate_page": None, "previous_candidate_page": None, "results": []}
    rejected = []
    for params in ({"q": "绝不匹配测试词", "page": 2}, {"q": "无线鼠标", "page": 2}, {"q": "无线鼠标", "page": 500}):
        response = request_search(**params)
        assert response.status_code == 404
        rejected.append({"params": params, "status": response.status_code, "response": response.json()})
    record(evidence, "zero_candidates_and_tail", {"first_empty_page": empty, "tail_rejections": rejected})


@online_only
def test_real_index_metadata_rejects_incomplete_wrong_source_and_missing_index(prototype, catalog, settings, evidence):
    built = prototype.rebuild()
    index = built["index"]
    enable_reader(settings, index)
    good_meta = {"search_schema": 1, "source_database": TEST_DATABASE, "build_complete": True}
    assert prototype.client.indices.get_mapping(index=index)[index]["mappings"]["_meta"] == good_meta
    rejected = []
    for bad_meta in (
        {**good_meta, "build_complete": False}, {**good_meta, "source_database": "project_one"},
        {**good_meta, "search_schema": True}, {**good_meta, "build_complete": 1},
    ):
        prototype.client.indices.put_mapping(index=index, _meta=bad_meta)
        response = request_search(q="无线鼠标")
        assert response.status_code == 503
        rejected.append({"injected_metadata": bad_meta, "status": response.status_code, "response": response.json()})
    prototype.client.indices.put_mapping(index=index, _meta=good_meta)
    restored = payload(request_search(q="无线鼠标"))
    assert restored["candidate_count"] == 2
    missing_index = INDEX_PREFIX + uuid4().hex
    settings.CATALOG_SEARCH_INDEX = missing_index
    missing = request_search(q="无线鼠标")
    assert missing.status_code == 503
    assert not prototype.client.indices.exists(index=missing_index)
    record(evidence, "unfinished_or_wrong_source_index", {
        "build": built, "metadata_rejections": rejected, "restored_response": restored,
        "missing_index_status": missing.status_code, "missing_index_created": False,
    })


def test_disabled_invalid_parameters_and_post_refuse_before_reader(settings, monkeypatch, evidence):
    calls = []

    def forbidden_reader():
        calls.append("constructed")
        pytest.fail("Disabled/invalid/method-rejected requests must not construct an ES reader.")

    monkeypatch.setattr(views, "SearchIndexReader", forbidden_reader)
    settings.CATALOG_SEARCH_ENABLED = False
    disabled = request_search(q="无线鼠标")
    assert disabled.status_code == 503
    settings.CATALOG_SEARCH_ENABLED = True
    invalid = (
        {}, {"q": ""}, {"q": " \t "}, {"q": "x" * 81}, {"q": "bad\x00keyword"},
        {"q": "无线鼠标", "page": 0}, {"q": "无线鼠标", "page": -1},
        {"q": "无线鼠标", "page": 501}, {"q": "无线鼠标", "page": "abc"},
        {"q": "无线鼠标", "page": "1.5"}, {"q": "无线鼠标", "category": "x" * 81},
    )
    statuses = []
    for params in invalid:
        response = request_search(**params)
        assert response.status_code == 400
        statuses.append(response.status_code)
    posted = APIClient(enforce_csrf_checks=True).post(reverse("catalog:product-search"), {"q": "无线鼠标"}, format="json")
    assert posted.status_code == 405 and calls == []
    record(evidence, "pre_reader_rejections", {
        "disabled_status": disabled.status_code, "invalid_statuses": statuses,
        "post_status": posted.status_code, "reader_constructions": len(calls), "database_fixture": False,
    })


def test_more_than_search_window_candidates_requires_narrower_query(settings, monkeypatch, evidence):
    calls = []

    class OversizedCandidateReader:
        page_size = 20
        page_limit = 500

        def candidates(self, keyword, category, page):
            calls.append({"keyword": keyword, "category": category, "page": page})
            return list(range(1, 21)), 10001

    monkeypatch.setattr(views, "SearchIndexReader", OversizedCandidateReader)
    settings.CATALOG_SEARCH_ENABLED = True
    response = request_search(q="无线鼠标")
    assert response.status_code == 400 and "q" in response.json()
    assert calls == [{"keyword": "无线鼠标", "category": None, "page": 1}]
    record(evidence, "candidate_window_guard_injected", {
        "injected_candidate_count": 10001, "status": response.status_code, "response": response.json(),
        "reader_calls": calls, "real_es": False, "database_fixture": False,
    })


def test_incomplete_or_malformed_es_results_fail_closed_with_injected_client(settings, monkeypatch, evidence):
    index = INDEX_PREFIX + "a" * 32
    enable_reader(settings, index)
    settings.ENVIRONMENT = "test"

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, sql):
            assert sql == "SELECT DATABASE()"

        def fetchone(self):
            return (TEST_DATABASE,)

    # Replace this module's reference only; the real Django target remains untouched.
    monkeypatch.setattr(search_read, "connection", SimpleNamespace(
        vendor="mysql", settings_dict={"NAME": TEST_DATABASE}, cursor=FakeCursor,
        get_autocommit=lambda: True, in_atomic_block=False,
    ))
    current = {"response": None}

    class FakeESClient:
        def __init__(self, *args, **kwargs):
            self.indices = SimpleNamespace(get_mapping=lambda **kwargs: {
                index: {"mappings": {"_meta": {
                    "search_schema": 1, "source_database": TEST_DATABASE, "build_complete": True,
                }}},
            })

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def info(self):
            return {"cluster_name": CLUSTER_NAME, "version": {"number": "8.19.23"}}

        def search(self, **kwargs):
            return current["response"]

    monkeypatch.setattr(search_read, "Elasticsearch", FakeESClient)
    valid = {
        "timed_out": False, "_shards": {"failed": 0},
        "hits": {"total": {"value": 1, "relation": "eq"}, "hits": [{"fields": {"product_id": [1]}}]},
    }
    bad_results = []
    timed_out = deepcopy(valid)
    timed_out["timed_out"] = True
    bad_results.append(("timed_out", timed_out))
    failed_shard = deepcopy(valid)
    failed_shard["_shards"]["failed"] = 1
    bad_results.append(("failed_shard", failed_shard))
    approximate_total = deepcopy(valid)
    approximate_total["hits"]["total"]["relation"] = "gte"
    bad_results.append(("approximate_total", approximate_total))
    empty_fields = deepcopy(valid)
    empty_fields["hits"]["hits"][0]["fields"]["product_id"] = []
    bad_results.append(("malformed_empty_id", empty_fields))
    boolean_id = deepcopy(valid)
    boolean_id["hits"]["hits"][0]["fields"]["product_id"] = [True]
    bad_results.append(("boolean_id", boolean_id))
    observations = []
    for label, response_data in bad_results:
        current["response"] = response_data
        response = request_search(q="无线鼠标")
        assert response.status_code == 503
        observations.append({"injection": label, "status": response.status_code})
    record(evidence, "incomplete_or_malformed_response_injected", {
        "results": observations, "real_es": False, "database_fixture": False,
    })


@pytest.mark.skipif(not OFFLINE, reason="explicit offline flag; stop isolated ES before this separate run")
def test_real_offline_search_returns_503_while_mysql_browse_still_works(guarded_db, settings, monkeypatch, evidence):
    category = Category.objects.create(name="故障浏览", slug="read-offline", is_active=True)
    Product.objects.create(category=category, slug="offline-product", name="无线鼠标", is_active=True)
    enable_reader(settings, INDEX_PREFIX + uuid4().hex)
    observed = []
    original_reader = views.SearchIndexReader

    class ObservedReader(original_reader):
        def candidates(self, *args, **kwargs):
            started = perf_counter()
            try:
                return super().candidates(*args, **kwargs)
            except (ApiError, TransportError) as error:
                observed.append({"error_type": type(error).__name__, "elapsed_seconds": perf_counter() - started})
                raise

    monkeypatch.setattr(views, "SearchIndexReader", ObservedReader)
    with CaptureQueriesContext(connection) as queries:
        failed = request_search(q="无线鼠标")
    search_sql = list(queries)
    assert failed.status_code == 503
    assert len(observed) == 1 and observed[0]["error_type"] in ("ConnectionError", "ConnectionTimeout")
    assert len(search_sql) == 1 and search_sql[0]["sql"] == "SELECT DATABASE()"
    browsed = APIClient().get(reverse("catalog:product-list"), {"q": "无线鼠标"})
    assert browsed.status_code == 200
    assert browsed.json() == {
        "count": 1, "next": None, "previous": None,
        "results": [public_summary("offline-product", "无线鼠标", "read-offline", "故障浏览")],
    }
    assert_actual_test_database()
    record(evidence, "real_offline_search_mysql_browse", {
        "search_status": failed.status_code, "search_response": failed.json(),
        "browse_response": browsed.json(), "built_index": False,
        "real_transport_failure": observed[0], "search_sql_count": len(search_sql), "product_summary_sql": 0,
    })

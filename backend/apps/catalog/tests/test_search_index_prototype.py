"""真实隔离 ES 原型证据；公开 API 仍使用 MySQL，测试不替换搜索入口。"""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.conf import settings
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from elasticsearch import Elasticsearch
from elasticsearch.helpers import BulkIndexError
from elastic_transport import ConnectionError, ConnectionTimeout
from rest_framework.test import APIClient

from apps.catalog import search_prototype
from apps.catalog.models import Category, Product
from apps.catalog.search_prototype import SearchIndexPrototype


TEST_URL = os.getenv("PROJECT_ONE_TEST_ELASTICSEARCH_URL")
OFFLINE = os.getenv("PROJECT_ONE_TEST_ES_OFFLINE") == "1"
ONLINE = bool(TEST_URL) and not OFFLINE
TEST_DATABASE = "test_project_one"
pytestmark = pytest.mark.elasticsearch_integration
online_only = pytest.mark.skipif(not ONLINE, reason="explicit isolated Elasticsearch URL required")
isolated_mysql = pytest.mark.django_db(transaction=True)

if ONLINE and (
    settings.ENVIRONMENT != "test"
    or settings.DATABASES["default"]["TEST"]["NAME"] != TEST_DATABASE
    or settings.DATABASES["default"]["NAME"] == TEST_DATABASE
):
    raise RuntimeError("Search index evidence requires DJANGO_ENV=test and isolated test_project_one.")


@pytest.fixture(scope="session")
def evidence(request):
    path_text = os.getenv("PROJECT_ONE_SEARCH_INDEX_REPORT")
    report_path = Path(path_text) if path_text else None
    if report_path:
        assert report_path.is_absolute() and report_path.parent.is_dir()
        assert not report_path.exists(), "Refuse to overwrite previous evidence."
    report = {"date": "2026-10-10", "observations": [], "cleanup": []}
    yield report
    report["outcome"] = "failed" if request.session.testsfailed or report.get("cleanup_failed") else "passed"
    report["boundary"] = (
        "Local controlled documents and real ES requests; no public API switch, "
        "automatic synchronization, production capacity, or general relevance benchmark."
    )
    if report_path:
        with report_path.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")


def record(evidence, label, result):
    evidence["observations"].append({"scenario": label, **result})
    print("search_index_prototype " + json.dumps({"scenario": label, **result}, ensure_ascii=False))


def assert_actual_test_database():
    assert connection.vendor == "mysql" and connection.settings_dict["NAME"] == TEST_DATABASE
    with connection.cursor() as cursor:
        cursor.execute("SELECT DATABASE()")
        assert cursor.fetchone()[0] == TEST_DATABASE


@pytest.fixture
def prototype(evidence):
    assert_actual_test_database()
    assert connection.get_autocommit() and not connection.in_atomic_block
    instance = SearchIndexPrototype(TEST_URL)
    info = dict(instance.client.info())
    evidence["elasticsearch"] = {"cluster": info["cluster_name"], "version": info["version"]["number"]}
    try:
        yield instance
    finally:
        owned = sorted(instance._indices)
        try:
            instance.close()
            with Elasticsearch(TEST_URL, request_timeout=2, max_retries=0) as control:
                remaining = [index for index in owned if control.indices.exists(index=index)]
            evidence["cleanup"].append({"owned_indices": owned, "remaining": remaining})
            assert not remaining, "Prototype must remove only its own exact candidate indices."
        except Exception:
            evidence["cleanup_failed"] = True
            raise


@pytest.fixture
def catalog():
    assert_actual_test_database()
    selected = Category.objects.create(name="原型鼠标", slug="prototype-mice", is_active=True)
    other = Category.objects.create(name="原型配件", slug="prototype-accessories", is_active=True)
    hidden = Category.objects.create(name="原型隐藏", slug="prototype-hidden", is_active=False)
    products = {}
    # The name-only / description-only pair has equal field lengths and swapped text.
    # It supports this fixture's name boost observation, not a universal score ratio.
    specifications = (
        (selected, "name-hit", "无线鼠标", "轻巧便携", True),
        (selected, "description-hit", "轻巧便携", "无线鼠标", True),
        (other, "other-category", "无线鼠标", "其他配件", True),
        (selected, "no-sku", "无线鼠标", "尚无规格", True),
        (selected, "inactive-product", "无线鼠标", "下架商品", False),
        (hidden, "inactive-category", "无线鼠标", "隐藏分类", True),
        (selected, "tie-first", "同款键盘", "耐用外壳", True),
        (selected, "tie-second", "同款键盘", "耐用外壳", True),
        (selected, "ascii-model", "无线鼠标 M1", "英文型号", True),
        (selected, "spaced-name", "无线 鼠标", "空格分隔", True),
        (selected, "cross-field", "蓝牙", "耳机", True),
        (selected, "same-field", "蓝牙 耳机", "音频配件", True),
    )
    for category, slug, name, description, active in specifications:
        products[slug] = Product.objects.create(
            category=category, slug=slug, name=name, description=description, is_active=active,
        )
    return {"selected": selected, "other": other, "hidden": hidden, "products": products}


def search_result(prototype, index, q="", category=None):
    result = prototype.search(index, q=q, category=category)
    assert result["_shards"]["failed"] == 0
    assert result["hits"]["total"]["relation"] == "eq"
    assert result["hits"]["total"]["value"] == len(result["hits"]["hits"])
    return result


def hit_slugs(result):
    return [hit["_source"]["slug"] for hit in result["hits"]["hits"]]


def mysql_result(q):
    response = APIClient().get(reverse("catalog:product-list"), {"q": q})
    assert response.status_code == 200
    return response.json()


@online_only
@isolated_mysql
def test_real_analyzers_search_semantics_and_mysql_comparison(prototype, catalog, evidence):
    build = prototype.rebuild()
    index = build["index"]
    assert build["documents"] == 10
    assert set(prototype._ready) == {index}
    definitions = dict(prototype.client.indices.get_mapping(index=index))
    tokens = {
        f"{analyzer}:{text}": prototype.analyze(index, text, analyzer=analyzer)
        for analyzer in ("catalog_cjk", "standard")
        for text in ("无线鼠标", "无线 鼠标", "标", "M1")
    }
    token_values = lambda analyzer, text: [item["token"] for item in tokens[f"{analyzer}:{text}"]["tokens"]]
    assert token_values("catalog_cjk", "无线鼠标") == ["无线", "线鼠", "鼠标"]
    assert token_values("catalog_cjk", "无线 鼠标") == ["无线", "鼠标"]
    assert token_values("standard", "无线鼠标") == ["无", "线", "鼠", "标"]
    assert token_values("catalog_cjk", "标") == ["标"]
    assert token_values("catalog_cjk", "M1") == ["m1"]

    queries = {
        "whole_keyword": search_result(prototype, index, "无线鼠标"),
        "separated_terms": search_result(prototype, index, "无线 鼠标"),
        "reordered_terms": search_result(prototype, index, "鼠标 无线"),
        "category": search_result(prototype, index, "无线鼠标", "prototype-mice"),
        "hidden_category": search_result(prototype, index, category="prototype-hidden"),
        "cross_field_and": search_result(prototype, index, "蓝牙 耳机"),
        "tie": search_result(prototype, index, "键盘"),
        "ascii": search_result(prototype, index, "m1"),
        "single_character": search_result(prototype, index, "标"),
        "missing": search_result(prototype, index, "不可能匹配"),
        "empty": search_result(prototype, index),
    }
    whole = {"name-hit", "description-hit", "other-category", "no-sku", "ascii-model"}
    separated = whole | {"spaced-name"}
    assert set(hit_slugs(queries["whole_keyword"])) == whole
    assert set(hit_slugs(queries["separated_terms"])) == separated
    assert set(hit_slugs(queries["reordered_terms"])) == separated
    assert set(hit_slugs(queries["category"])) == whole - {"other-category"}
    assert hit_slugs(queries["hidden_category"]) == []
    assert hit_slugs(queries["cross_field_and"]) == ["same-field"]
    assert hit_slugs(queries["tie"]) == ["tie-first", "tie-second"]
    tie_scores = [hit["_score"] for hit in queries["tie"]["hits"]["hits"]]
    assert tie_scores[0] == tie_scores[1]
    assert hit_slugs(queries["ascii"]) == ["ascii-model"]
    assert hit_slugs(queries["single_character"]) == []
    assert hit_slugs(queries["missing"]) == []
    visible = set(catalog["products"]) - {"inactive-product", "inactive-category"}
    assert set(hit_slugs(queries["empty"])) == visible
    empty_ids = [hit["_source"]["product_id"] for hit in queries["empty"]["hits"]["hits"]]
    assert empty_ids == sorted(catalog["products"][slug].pk for slug in visible)
    scores = {hit["_source"]["slug"]: hit["_score"] for hit in queries["whole_keyword"]["hits"]["hits"]}
    assert scores["name-hit"] > scores["description-hit"]

    mysql = {query: mysql_result(query) for query in ("无线鼠标", "无线 鼠标", "鼠标 无线", "标")}
    assert {item["slug"] for item in mysql["无线鼠标"]["results"]} == whole
    assert [item["slug"] for item in mysql["无线 鼠标"]["results"]] == ["spaced-name"]
    assert mysql["鼠标 无线"]["count"] == 0
    assert {item["slug"] for item in mysql["标"]["results"]} == separated
    no_sku = next(item for item in mysql["无线鼠标"]["results"] if item["slug"] == "no-sku")
    assert no_sku["lowest_price"] is None and no_sku["in_stock"] is False
    with pytest.raises(ValueError, match="completed indices"):
        prototype.search("project-one-development")
    record(evidence, "analyzers_and_search", {
        "build": build, "mapping": definitions, "analyze": tokens,
        "searches": queries, "mysql_api": mysql,
        "score_pair": {slug: scores[slug] for slug in ("name-hit", "description-hit")},
    })


@online_only
@isolated_mysql
def test_rebuild_reflects_source_changes_while_old_index_remains_stale(prototype, catalog, evidence):
    first = prototype.rebuild()
    products = catalog["products"]
    Product.objects.filter(pk=products["name-hit"].pk).update(name="有线键盘")
    Product.objects.filter(pk=products["description-hit"].pk).update(is_active=False)
    Category.objects.filter(pk=catalog["other"].pk).update(is_active=False)
    added = Product.objects.create(
        category=catalog["selected"], slug="new-after-build", name="无线鼠标", is_active=True,
    )
    old_result = search_result(prototype, first["index"], "无线鼠标")
    assert set(hit_slugs(old_result)) == {"name-hit", "description-hit", "other-category", "no-sku", "ascii-model"}
    second = prototype.rebuild()
    assert first["index"] != second["index"] and second["documents"] == 9
    new_result = search_result(prototype, second["index"], "无线鼠标")
    assert set(hit_slugs(new_result)) == {"no-sku", "ascii-model", "new-after-build"}
    renamed = search_result(prototype, second["index"], "有线键盘")
    assert hit_slugs(renamed) == ["name-hit"]
    assert set(hit_slugs(search_result(prototype, first["index"], "无线鼠标"))) == set(hit_slugs(old_result))
    new_hit = next(hit for hit in new_result["hits"]["hits"] if hit["_source"]["slug"] == "new-after-build")
    assert new_hit["_source"]["product_id"] == added.pk
    record(evidence, "rebuild_and_stale_index", {
        "before": first, "after": second, "old_result": old_result,
        "new_result": new_result, "renamed_result": renamed,
    })


@online_only
@isolated_mysql
def test_real_bulk_partial_failure_never_marks_candidate_ready(prototype, catalog, monkeypatch, evidence):
    first = prototype.rebuild()
    before = search_result(prototype, first["index"], "无线鼠标")
    documents = search_prototype.product_documents()
    invalid = {**documents[0], "product_id": "invalid", "slug": "invalid-bulk-document"}
    with monkeypatch.context() as patch:
        patch.setattr(search_prototype, "product_documents", lambda: [documents[0], invalid])
        with pytest.raises(BulkIndexError) as error:
            prototype.rebuild()
    failed = prototype._indices - {first["index"]}
    assert len(failed) == 1 and prototype._ready == {first["index"]}
    failed_index = failed.pop()
    assert error.value.errors
    actual_error = error.value.errors[0]["index"]
    assert actual_error["status"] == 400 and actual_error["error"]["type"] == "document_parsing_exception"
    prototype.client.indices.refresh(index=failed_index)
    failed_count = prototype.client.count(index=failed_index)["count"]
    assert failed_count == 1
    with pytest.raises(ValueError, match="completed indices"):
        prototype.search(failed_index)
    after = search_result(prototype, first["index"], "无线鼠标")
    assert after["hits"] == before["hits"]
    assert prototype.client.count(index=first["index"])["count"] == 10
    record(evidence, "real_bulk_partial_failure", {
        "completed": first, "failed_index": failed_index, "failed_index_documents": failed_count,
        "bulk_errors": error.value.errors, "completed_search_before": before, "completed_search_after": after,
    })


@online_only
def test_url_and_environment_guards_refuse_before_creating_client(monkeypatch, evidence):
    def unexpected_client(*args, **kwargs):
        pytest.fail("Rejected configuration must not construct an ES client.")
    monkeypatch.setattr(search_prototype, "Elasticsearch", unexpected_client)
    rejected = (
        "http://127.0.0.1:9200", "http://localhost:9201", "http://example.com:9201",
        "http://127.0.0.1:9201/development", "http://127.0.0.1:9201?target=development",
    )
    for url in rejected:
        with pytest.raises(ValueError, match="isolated ES test service"):
            SearchIndexPrototype(url)
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    with pytest.raises(ValueError, match="isolated ES test service"):
        SearchIndexPrototype("http://127.0.0.1:9201")
    record(evidence, "connection_configuration_guards", {"rejected_urls": list(rejected), "rejected_environment": "development"})


@online_only
@isolated_mysql
def test_source_guard_refuses_development_database_and_outer_transaction(monkeypatch, evidence):
    assert_actual_test_database()
    with monkeypatch.context() as patch:
        # 拒绝分支使用替身；绝不能为测试而改变真实连接的数据库目标。
        patch.setattr(search_prototype, "connection", SimpleNamespace(
            vendor="mysql", settings_dict={"NAME": "project_one"},
        ))
        with CaptureQueriesContext(connection) as queries:
            with pytest.raises(RuntimeError, match="isolated MySQL"):
                search_prototype.product_documents()
        assert len(queries) == 0
    with transaction.atomic():
        with CaptureQueriesContext(connection) as queries:
            with pytest.raises(RuntimeError, match="hold a MySQL transaction"):
                search_prototype.product_documents()
        assert len(queries) == 0
    assert connection.settings_dict["NAME"] == TEST_DATABASE
    assert connection.get_autocommit() and not connection.in_atomic_block
    record(evidence, "source_configuration_guards", {"rejected_source": "project_one", "rejected_outer_atomic": True, "product_sql_in_each_rejection": 0})


@pytest.mark.skipif(not OFFLINE, reason="explicit offline flag; stop isolated ES before this separate run")
def test_real_offline_connection_fails_without_database(evidence):
    # Docker Desktop关闭服务后可能表现为连接拒绝或超时，均须明确失败。
    with pytest.raises((ConnectionError, ConnectionTimeout)) as error:
        SearchIndexPrototype("http://127.0.0.1:9201")
    record(evidence, "real_offline_connection", {"error_type": type(error.value).__name__, "database_fixture": False})

"""隔离搜索索引构建器：不允许操作开发索引，没有自动同步。"""

import re
from urllib.parse import urlsplit
from uuid import uuid4

from django.conf import settings
from django.db import connection
from elasticsearch import Elasticsearch, helpers

from apps.catalog.models import Product
from apps.catalog.serializers import ProductSearchQuerySerializer


INDEX_PREFIX = "project-one-test-search-"
CLUSTER_NAME = "project-one-search-index-test"
SOURCE_DATABASE = "test_project_one"


def search_query(keyword, category=None):
    text_query = {"multi_match": {
        "query": keyword, "fields": ["name^3", "description"],
        "type": "best_fields", "operator": "and", "zero_terms_query": "none",
    }} if keyword else {"match_all": {}}
    query = {"bool": {"must": [text_query], "filter": []}}
    if category:
        query["bool"]["filter"].append({"term": {"category_slug": category}})
    return query


def build_metadata(complete):
    return {"search_schema": 1, "source_database": SOURCE_DATABASE, "build_complete": complete}


def is_candidate_index(index):
    return isinstance(index, str) and bool(re.fullmatch(re.escape(INDEX_PREFIX) + r"[0-9a-f]{32}", index))


def is_completed_metadata(meta):
    return (
        isinstance(meta, dict) and meta == build_metadata(True)
        and meta.get("build_complete") is True and type(meta.get("search_schema")) is int
    )


def require_test_source(db_connection):
    if (
        settings.ENVIRONMENT != "test"
        or db_connection.vendor != "mysql"
        or db_connection.settings_dict["NAME"] != SOURCE_DATABASE
    ):
        raise RuntimeError("Search prototype requires isolated MySQL test_project_one.")
    if not db_connection.get_autocommit() or db_connection.in_atomic_block:
        raise RuntimeError("Do not hold a MySQL transaction while building a search index.")
    with db_connection.cursor() as cursor:
        cursor.execute("SELECT DATABASE()")
        if cursor.fetchone()[0] != SOURCE_DATABASE:
            raise RuntimeError("Search prototype requires the actual isolated MySQL database.")


def product_documents():
    require_test_source(connection)
    # 先物化单条SELECT的结果，再等待ES；不是跨存储的一致快照或增量同步。
    rows = Product.objects.filter(is_active=True, category__is_active=True).order_by("id").values(
        "id", "slug", "category__slug", "name", "description",
    )
    return [
        {
            "product_id": row["id"], "slug": row["slug"],
            "category_slug": row["category__slug"],
            "name": row["name"], "description": row["description"],
        }
        for row in rows
    ]


def index_definition():
    return {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
            "analysis": {"analyzer": {"catalog_cjk": {"type": "cjk", "stopwords": []}}},
        },
        "mappings": {
            "_meta": build_metadata(False),
            "dynamic": "strict",
            "properties": {
                "product_id": {"type": "long"},
                "slug": {"type": "keyword"},
                "category_slug": {"type": "keyword"},
                "name": {"type": "text", "analyzer": "catalog_cjk"},
                "description": {"type": "text", "analyzer": "catalog_cjk"},
            },
        },
    }


class SearchIndexPrototype:
    def __init__(self, url):
        parts = urlsplit(url)
        if settings.ENVIRONMENT != "test" or (
            parts.scheme != "http" or parts.hostname != "127.0.0.1" or parts.port != 9201
            or parts.path not in ("", "/") or parts.username or parts.password
            or parts.query or parts.fragment
        ):
            raise ValueError("Only the local isolated ES test service on port 9201 is allowed.")
        self.client = Elasticsearch(url, request_timeout=2, max_retries=0, retry_on_timeout=False)
        self._indices = set()
        self._ready = set()
        try:
            info = self.client.info()
            if info["cluster_name"] != CLUSTER_NAME or not info["version"]["number"].startswith("8.19."):
                raise RuntimeError("Unexpected Elasticsearch test cluster or version.")
        except Exception:
            self.client.close()
            raise

    def rebuild(self):
        documents = product_documents()
        index = INDEX_PREFIX + uuid4().hex
        self.client.indices.create(index=index, **index_definition())
        self._indices.add(index)
        # HTTP成功不等于Bulk每一项成功；任何项失败都不把候选标为可查询。
        written, _ = helpers.bulk(
            self.client,
            ({"_index": index, "_id": str(doc["product_id"]), "_source": doc} for doc in documents),
            chunk_size=200, max_retries=0, raise_on_error=True, raise_on_exception=True,
        )
        refreshed = self.client.indices.refresh(index=index)
        counted = self.client.count(index=index)
        if refreshed["_shards"]["failed"] or counted["_shards"]["failed"]:
            raise RuntimeError("Search index validation returned failed shards.")
        actual = counted["count"]
        if written != len(documents) or actual != len(documents):
            raise RuntimeError("Incomplete search index build.")
        self.client.indices.put_mapping(index=index, _meta=build_metadata(True))
        self._ready.add(index)
        return {"index": index, "documents": actual}

    def search(self, index, q="", category=None):
        if index not in self._ready:
            raise ValueError("Only this prototype's completed indices can be queried.")
        serializer = ProductSearchQuerySerializer(data={"q": q})
        serializer.is_valid(raise_exception=True)
        keyword = serializer.validated_data["q"]
        return dict(self.client.search(
            index=index, query=search_query(keyword, category), size=20, track_total_hits=True,
            sort=[{"_score": "desc"}, {"product_id": "asc"}],
            allow_partial_search_results=False,
        ))

    def analyze(self, index, text, analyzer="catalog_cjk"):
        if index not in self._ready or analyzer not in ("catalog_cjk", "standard"):
            raise ValueError("Analyze only a completed index with the two compared analyzers.")
        return dict(self.client.indices.analyze(index=index, analyzer=analyzer, text=text))

    def close(self):
        failures = []
        try:
            for index in sorted(self._indices):
                try:
                    self.client.indices.delete(index=index)
                except Exception:
                    failures.append(index)
        finally:
            self.client.close()
        if failures:
            raise RuntimeError(f"Could not clean prototype indices: {failures}")

"""只读搜索适配器；本片只接隔离测试来源，默认关闭。"""

import re
from urllib.parse import urlsplit

from django.conf import settings
from django.db import connection
from elasticsearch import Elasticsearch

from apps.catalog.search_prototype import (
    CLUSTER_NAME, INDEX_PREFIX, SOURCE_DATABASE, build_metadata, search_query,
)


class SearchReadUnavailable(Exception):
    pass


class SearchIndexReader:
    page_size = 20
    page_limit = 500

    def candidates(self, keyword, category, page):
        # 不允许把测试索引里的ID带去读取开发商品，或持有事务等待ES。
        if (
            settings.ENVIRONMENT != "test" or connection.vendor != "mysql"
            or connection.settings_dict["NAME"] != SOURCE_DATABASE
            or not connection.get_autocommit() or connection.in_atomic_block
        ):
            raise SearchReadUnavailable("Search requires the isolated source database.")
        with connection.cursor() as cursor:
            cursor.execute("SELECT DATABASE()")
            if cursor.fetchone()[0] != SOURCE_DATABASE:
                raise SearchReadUnavailable("Unexpected actual search source database.")

        index = settings.CATALOG_SEARCH_INDEX
        parts = urlsplit(settings.CATALOG_SEARCH_URL)
        if not re.fullmatch(re.escape(INDEX_PREFIX) + r"[0-9a-f]{32}", index) or (
            parts.scheme != "http" or parts.hostname != "127.0.0.1" or parts.port != 9201
            or parts.path not in ("", "/") or parts.username or parts.password
            or parts.query or parts.fragment
        ):
            raise SearchReadUnavailable("Unexpected isolated search configuration.")

        with Elasticsearch(
            settings.CATALOG_SEARCH_URL, request_timeout=2, max_retries=0,
            retry_on_timeout=False,
        ) as client:
            try:
                return self._read(client, index, keyword, category, page)
            except (KeyError, IndexError, TypeError, AttributeError) as exc:
                raise SearchReadUnavailable("Invalid search response structure.") from exc

    def _read(self, client, index, keyword, category, page):
        info = client.info()
        if info["cluster_name"] != CLUSTER_NAME or not info["version"]["number"].startswith("8.19."):
            raise SearchReadUnavailable("Unexpected search cluster or version.")
        mapping = client.indices.get_mapping(index=index)
        meta = mapping.get(index, {}).get("mappings", {}).get("_meta", {})
        if (
            set(mapping) != {index} or meta != build_metadata(True)
            or meta.get("build_complete") is not True or type(meta.get("search_schema")) is not int
        ):
            raise SearchReadUnavailable("Search index is unfinished or has a different source.")
        result = client.search(
            index=index, query=search_query(keyword, category),
            from_=(page - 1) * self.page_size, size=self.page_size,
            track_total_hits=True, sort=[{"_score": "desc"}, {"product_id": "asc"}],
            source=False, fields=["product_id"], allow_partial_search_results=False,
        )
        # HTTP200仍可能是超时/不完整结果，不能当作正常空结果。
        if result["timed_out"] or result["_shards"]["failed"]:
            raise SearchReadUnavailable("Incomplete search result.")
        total = result["hits"]["total"]
        if total["relation"] != "eq" or type(total["value"]) is not int or total["value"] < 0:
            raise SearchReadUnavailable("Invalid candidate count.")
        ids = [hit["fields"]["product_id"][0] for hit in result["hits"]["hits"]]
        expected_size = min(self.page_size, max(0, total["value"] - (page - 1) * self.page_size))
        if (
            len(ids) != expected_size or len(set(ids)) != len(ids)
            or any(type(product_id) is not int or product_id < 1 for product_id in ids)
        ):
            raise SearchReadUnavailable("Invalid search candidates.")
        return ids, total["value"]

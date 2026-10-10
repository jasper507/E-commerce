"""只读搜索适配器；本片只接隔离测试来源，默认关闭。"""

from urllib.parse import urlsplit

from django.conf import settings
from django.db import connection
from elasticsearch import Elasticsearch

from apps.catalog.search_prototype import (
    CLUSTER_NAME, is_candidate_index, is_completed_metadata, require_test_source, search_query,
)
from apps.catalog.search_alias import SearchAliasInvalid, is_read_alias, resolve_read_alias


class SearchReadUnavailable(Exception):
    pass


class SearchIndexReader:
    page_size = 20
    page_limit = 500

    def candidates(self, keyword, category, page):
        # 不允许把测试索引里的ID带去读取开发商品，或持有事务等待ES。
        try:
            require_test_source(connection)
        except RuntimeError as exc:
            raise SearchReadUnavailable(str(exc)) from exc

        index = settings.CATALOG_SEARCH_INDEX
        alias = settings.CATALOG_SEARCH_ALIAS
        parts = urlsplit(settings.CATALOG_SEARCH_URL)
        if (bool(index) == bool(alias)) or (index and not is_candidate_index(index)) or (alias and not is_read_alias(alias)) or (
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
                info = client.info()
                if info["cluster_name"] != CLUSTER_NAME or not info["version"]["number"].startswith("8.19."):
                    raise SearchReadUnavailable("Unexpected search cluster or version.")
                # 每个请求只解析一次，切换后仍读取这个精确旧索引，直到请求结束。
                if alias:
                    index = resolve_read_alias(client, alias)
                return self._read(client, index, keyword, category, page)
            except (KeyError, IndexError, TypeError, AttributeError, SearchAliasInvalid) as exc:
                raise SearchReadUnavailable("Invalid search response structure.") from exc

    def _read(self, client, index, keyword, category, page):
        mapping = client.indices.get_mapping(index=index)
        meta = mapping.get(index, {}).get("mappings", {}).get("_meta", {})
        if set(mapping) != {index} or not is_completed_metadata(meta):
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

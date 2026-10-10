"""隔离环境的手动索引切换；调用者负责单操作者与同一来源生命周期。"""

import re

from django.db import connection
from elasticsearch import ApiError, NotFoundError
from elastic_transport import TransportError

from apps.catalog.search_prototype import (
    is_candidate_index, is_completed_metadata, require_test_source,
)


READ_ALIAS_PREFIX = "project-one-test-search-read-"


class SearchAliasInvalid(RuntimeError):
    pass


class SearchAliasConflict(RuntimeError):
    pass


def is_read_alias(alias):
    return isinstance(alias, str) and bool(re.fullmatch(re.escape(READ_ALIAS_PREFIX) + r"[0-9a-f]{32}", alias))


def resolve_read_alias(client, alias, *, missing_ok=False):
    if not is_read_alias(alias):
        raise SearchAliasInvalid("Only an exact isolated read alias is allowed.")
    try:
        response = client.indices.get_alias(name=alias)
    except NotFoundError:
        if missing_ok:
            return None
        raise
    try:
        if len(response) != 1:
            raise SearchAliasInvalid("Read alias must refer to exactly one candidate.")
        index = next(iter(response))
        # 解析后按精确索引搜索会绕过别名过滤/路由，因此仅允许本片创建的空配置。
        if not is_candidate_index(index) or response[index].get("aliases", {}).get(alias) != {}:
            raise SearchAliasInvalid("Unexpected read alias target or options.")
    except (KeyError, TypeError, AttributeError) as exc:
        raise SearchAliasInvalid("Invalid read alias response.") from exc
    return index


class SearchIndexPublisher:
    def __init__(self, prototype, alias):
        if not is_read_alias(alias):
            raise SearchAliasInvalid("Only an exact isolated read alias is allowed.")
        self.prototype = prototype
        self.alias = alias

    def rebuild_and_publish(self, *, expected_index):
        # 构建异常直接返回调用者；此时根本没有提交别名切换，不清理旧索引。
        self._check_expected(expected_index)
        built = self.prototype.rebuild()
        return self.publish(built["index"], expected_index=expected_index)

    def _check_expected(self, expected_index):
        require_test_source(connection)
        if expected_index is not None and expected_index not in self.prototype._ready:
            raise SearchAliasConflict("Expected target must belong to this completed build session.")
        actual = resolve_read_alias(self.prototype.client, self.alias, missing_ok=True)
        if actual != expected_index:
            raise SearchAliasConflict("Read alias changed; inspect it before publishing again.")

    def publish(self, index, *, expected_index):
        self._check_expected(expected_index)
        if index not in self.prototype._ready or not is_candidate_index(index):
            raise SearchAliasInvalid("Only this build session's completed candidate can be published.")
        mapping = self.prototype.client.indices.get_mapping(index=index)
        if set(mapping) != {index} or not is_completed_metadata(mapping[index]["mappings"].get("_meta")):
            raise SearchAliasInvalid("Candidate is unfinished or has a different source.")
        actions = []
        if expected_index is not None:
            actions.append({"remove": {"index": expected_index, "alias": self.alias, "must_exist": True}})
        actions.append({"add": {"index": index, "alias": self.alias}})
        response, request_error = None, None
        try:
            response = dict(self.prototype.client.indices.update_aliases(
                actions=actions, master_timeout="2s", timeout="2s",
            ))
        except (ApiError, TransportError, TypeError, ValueError) as exc:
            request_error = type(exc).__name__
        try:
            response_ok = bool(
                response is not None and response.get("acknowledged") is True
                and response.get("errors", False) is False
                and all(item.get("status") == 200 and not item.get("error") for item in response.get("action_results", []))
            )
        except (TypeError, AttributeError):
            response_ok, request_error = False, "InvalidAliasResponse"
        # 超时或ack=False都不证明没切。只回读，不盲重发、反向切回或删除候选。
        try:
            current = resolve_read_alias(self.prototype.client, self.alias, missing_ok=True)
            state = "switched" if current == index else "unchanged" if current == expected_index else "unknown"
            readback_error = None
        except (ApiError, TransportError, SearchAliasInvalid) as exc:
            current, state, readback_error = None, "unknown", type(exc).__name__
        return {
            "candidate": index, "previous_index": expected_index, "current_index": current,
            "state": state, "response": response, "response_ok": response_ok, "request_error": request_error,
            "readback_error": readback_error,
        }

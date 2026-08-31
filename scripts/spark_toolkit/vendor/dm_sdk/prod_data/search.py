from typing import Any, Dict, List, Optional

from dm_sdk.models import RespBody
from dm_sdk.prod_data.base import ProdDataBasic
from dm_sdk.tools.api import ApiBaseError
from dm_sdk.tools.webapp_client import WebappClient


class SearchMixin(ProdDataBasic):
    """搜索 Mixin。"""

    def count_bags(self, condition: Dict, table: Optional[str] = None) -> int:
        """Count bags matching search condition."""
        return self._count_records(condition, table)

    def count_clips(self, condition: Dict, table: Optional[str] = None) -> int:
        """Count clips matching search condition."""
        return self._count_records(condition, table)

    def _count_records(
        self, condition: Dict, table: Optional[str] = None
    ) -> int:
        table = table or self.table
        payload = {"indexName": table, "condition": condition}
        resp = self._webapp_client.do_request(
            WebappClient.POST, "/es/dynamic-query-total", json=payload
        )
        if resp.get("code") != 200:
            raise ApiBaseError(
                f"查询记录总数失败: {resp.get('message', '')} (table={table})",
                trace_id=resp.trace_id,
            )
        return resp.get("data", {}).get("total", 0)

    def preview_bags(
        self,
        table: Optional[str] = None,
        result_fields: Optional[List[str]] = None,
        exclude_fields: Optional[List[str]] = None,
    ) -> RespBody:
        """Preview one bag record.

        Args:
            table: Table name. If not specified, uses the default table.
            result_fields: List of fields to include. If not specified, returns all fields.
            exclude_fields: List of fields to exclude. Applied after fetching data.

        Returns:
            RespBody containing 1 bag record with filtered fields.
        """
        resp = self._search_records(None, table, 1, None, result_fields)

        # Apply exclude_fields filter if provided
        if exclude_fields and resp.resp_data():
            data = resp.resp_data()
            if isinstance(data, dict) and "data" in data:
                # The actual records are in data['data'] which is a list
                records = data["data"]
                if isinstance(records, list) and len(records) > 0:
                    # Filter fields for each record
                    filtered_records = []
                    for record in records:
                        if isinstance(record, dict):
                            filtered_record = {
                                k: v
                                for k, v in record.items()
                                if k not in exclude_fields
                            }
                            filtered_records.append(filtered_record)
                        else:
                            filtered_records.append(record)

                    filtered_data = data.copy()
                    filtered_data["data"] = filtered_records
                    return resp.to_resp_body(data=filtered_data)

        return resp

    def preview_clips(
        self,
        table: Optional[str] = None,
        result_fields: Optional[List[str]] = None,
        exclude_fields: Optional[List[str]] = None,
    ) -> RespBody:
        """Preview one clip record.

        Args:
            table: Table name. If not specified, uses the default table.
            result_fields: List of fields to include. If not specified, returns all fields.
            exclude_fields: List of fields to exclude. Applied after fetching data.

        Returns:
            RespBody containing 1 clip record with filtered fields.
        """
        resp = self._search_records(None, table, 1, None, result_fields)

        # Apply exclude_fields filter if provided
        if exclude_fields and resp.resp_data():
            data = resp.resp_data()
            if isinstance(data, dict) and "data" in data:
                # The actual records are in data['data'] which is a list
                records = data["data"]
                if isinstance(records, list) and len(records) > 0:
                    # Filter fields for each record
                    filtered_records = []
                    for record in records:
                        if isinstance(record, dict):
                            filtered_record = {
                                k: v
                                for k, v in record.items()
                                if k not in exclude_fields
                            }
                            filtered_records.append(filtered_record)
                        else:
                            filtered_records.append(record)

                    filtered_data = data.copy()
                    filtered_data["data"] = filtered_records
                    return resp.to_resp_body(data=filtered_data)

        return resp

    def search_bags(
        self,
        condition: Dict,
        table: Optional[str] = None,
        size: int = 50,
        search_after: Optional[List[Any]] = None,
        result_fields: Optional[List[str]] = None,
    ) -> RespBody:
        """Search for bags based on condition."""
        if not (50 <= size <= 1000):
            raise ApiBaseError(
                f"每页查询数量size必须在50~1000之间 (size={size})"
            )
        return self._search_records(
            condition, table, size, search_after, result_fields
        )

    def search_clips(
        self,
        condition: Dict,
        table: Optional[str] = None,
        size: int = 50,
        search_after: Optional[List[Any]] = None,
        result_fields: Optional[List[str]] = None,
    ) -> RespBody:
        """Search for clips based on condition."""
        if not (50 <= size <= 1000):
            raise ApiBaseError(
                f"每页查询数量size必须在50~1000之间 (size={size})"
            )
        return self._search_records(
            condition, table, size, search_after, result_fields
        )

    def _search_records(
        self,
        condition=None,
        table=None,
        size=50,
        search_after=None,
        result_fields=None,
    ) -> RespBody:
        table = table or self.table
        payload = {
            "indexName": table,
            "size": size,
        }
        if condition:
            payload["condition"] = condition
        if search_after:
            payload["searchAfter"] = search_after
        if result_fields:
            payload["resultFields"] = result_fields
        resp = self._webapp_client.do_request(
            WebappClient.POST, "/es/dynamic-query", json=payload
        )
        return resp.to_resp_body()

    def iter_search_bags(
        self,
        condition: Dict,
        table: Optional[str] = None,
        size: int = 50,
        result_fields: Optional[List[str]] = None,
        limit: Optional[int] = None,
    ):
        """
        自动分页迭代查询bags，逐条返回记录。

        :param condition: 查询条件字典
        :param table: 索引名称
        :param size: 每页大小（50~1000）
        :param result_fields: 返回字段列表，如 ["create_time", "data_id_id"]
        :param limit: 最多返回多少条记录；为 None 时则一直迭代到结束
        """
        search_after = None
        yielded = 0
        table = table or self.table
        while True:
            resp = self.search_bags(
                condition=condition,
                table=table,
                size=size,
                search_after=search_after,
                result_fields=result_fields,
            )
            if resp.resp_code() != 200:
                raise ApiBaseError(
                    f"分页查询bags失败: {resp.msg} (table={table})",
                    trace_id=resp.trace_id,
                )

            data = resp.resp_data() or {}
            items = data.get("data", [])
            if isinstance(items, dict):
                items = items.get("data", items)

            next_search_after = data.get("nextSearchAfter")
            if not items:
                break
            for item in items:
                yield item
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
            if not next_search_after or next_search_after == search_after:
                break
            search_after = next_search_after

    def iter_search_clips(
        self,
        condition: Dict,
        table: Optional[str] = None,
        size: int = 50,
        result_fields: Optional[List[str]] = None,
        limit: Optional[int] = None,
    ):
        """
        自动分页迭代查询clips，逐条返回记录。

        :param condition: 查询条件字典
        :param table: 索引名称
        :param size: 每页大小（50~1000）
        :param result_fields: 返回字段列表，如 ["create_time", "data_id"]
        :param limit: 最多返回多少条记录；为 None 时则一直迭代到结束
        """
        search_after = None
        yielded = 0
        table = table or self.table
        while True:
            resp = self.search_clips(
                condition=condition,
                table=table,
                size=size,
                search_after=search_after,
                result_fields=result_fields,
            )
            if resp.resp_code() != 200:
                raise ApiBaseError(
                    f"分页查询clips失败: {resp.msg} (table={table})",
                    trace_id=resp.trace_id,
                )

            data = resp.resp_data() or {}
            items = data.get("data", [])
            if isinstance(items, dict):
                items = items.get("data", items)

            next_search_after = data.get("nextSearchAfter")
            if not items:
                break
            for item in items:
                yield item
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
            if not next_search_after or next_search_after == search_after:
                break
            search_after = next_search_after

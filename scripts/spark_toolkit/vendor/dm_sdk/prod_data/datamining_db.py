from typing import Any, Dict, Optional

from dm_sdk.models import RespBody
from dm_sdk.tools.webapp_client import WebappClient


class DataminingDbMixin:
    """Datamining DB 元数据管理 Mixin。"""

    def upsert_datamining_db(
        self,
        data_id: str,
        storage_prefix: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        table: Optional[str] = None,
    ) -> RespBody:
        """
        创建或更新 datamining db 元数据。

        :param data_id: bin id
        :param storage_prefix: db 在 oss 的存储路径
        :param metadata: 自定义元数据字典，如 generate_time, version, db_type
        :param table_name: 表名，不传默认使用实例的 table
        :return: RespBody(code, msg, data)
        """
        table = table or self.table

        payload: Dict[str, Any] = {
            "table": table,
            "data_id": data_id,
        }
        if storage_prefix is not None:
            payload["storage_prefix"] = storage_prefix
        if metadata is not None:
            payload["metadata"] = metadata

        resp = self._webapp_client.do_request(
            WebappClient.POST,
            "/mining-db/upsert",
            json=payload,
        )
        return resp.to_resp_body()

    def get_datamining_db(
        self,
        data_id: str,
        table: Optional[str] = None,
    ) -> RespBody:
        """
        获取 datamining db 元数据。

        :param data_id: bin id
        :param table: 表名，不传默认使用实例的 table
        :return: RespBody(code, msg, data)，data 格式:
            {
                "data_id": "",          // bin id
                "metadata": {},         // 自定义元数据
                "storage_prefix": "",   // db 在 OSS 的存储位置
                ...
            }
        """
        table = table or self.table

        resp = self._webapp_client.do_request(
            WebappClient.POST,
            "/mining-db/detail",
            json={
                "table": table,
                "data_id": data_id,
            },
        )
        return resp.to_resp_body()

from typing import Any, Dict, Optional

from dm_sdk.models import RespBody
from dm_sdk.prod_data.base import ProdDataBasic
from dm_sdk.tools.webapp_client import WebappClient


class TagMixin(ProdDataBasic):
    """标签（Tags）CRUD Mixin。"""

    def upsert_bag_tags(
        self,
        data_id: str,
        tag_source: str,
        tags: Dict[str, Any],
        table: Optional[str] = None,
        tags_replace: bool = True,
        version: str = "v_1_1",
    ) -> RespBody:
        """insert or update bag tags, by tag source."""
        result = self._upsert_tags(
            data_id, tag_source, tags, table, tags_replace, version
        )
        if result.resp_code() == 200:
            self._tracker.track(
                data_id, table or self.table, "upsert_bag_tags"
            )
        return result

    def upsert_clip_tags(
        self,
        data_id: str,
        tag_source: str,
        tags: Dict[str, Any],
        table: Optional[str] = None,
        tags_replace: bool = True,
        version: str = "v_1_1",
    ) -> RespBody:
        """insert or update clip tags, by tag source."""
        result = self._upsert_tags(
            data_id, tag_source, tags, table, tags_replace, version
        )
        if result.resp_code() == 200:
            self._tracker.track(
                data_id, table or self.table, "upsert_clip_tags"
            )
        return result

    def _upsert_tags(
        self,
        data_id: str,
        tag_source: str,
        tags: Dict[str, Any],
        table: Optional[str] = None,
        tags_replace: bool = True,
        version: str = "v_1_1",
    ) -> RespBody:
        table = table or self.table
        payload: Dict[str, Any] = {
            "table": table,
            "data_id": data_id,
            "tag_source": tag_source,
            "tags": tags,
            "tagsReplace": tags_replace,
            "version": version,
        }
        resp = self._webapp_client.do_request(
            WebappClient.POST, "/dataTags/upsert", json=payload
        )
        return resp.to_resp_body()

    def get_clip_tags(
        self,
        data_id: str,
        tag_source: Optional[str] = None,
        table: Optional[str] = None,
        version: Optional[str] = None,
    ) -> RespBody:
        """get clip tags, by tag source."""
        result = self._get_tags(data_id, tag_source, table, version)
        if result.resp_code() == 200:
            self._tracker.track(data_id, table or self.table, "get_clip_tags")
        return result

    def get_bag_tags(
        self,
        data_id: str,
        tag_source: Optional[str] = None,
        table: Optional[str] = None,
        version: Optional[str] = None,
    ) -> RespBody:
        """get bag tags, by tag source."""
        result = self._get_tags(data_id, tag_source, table, version)
        if result.resp_code() == 200:
            self._tracker.track(data_id, table or self.table, "get_bag_tags")
        return result

    def _get_tags(
        self,
        data_id: str,
        tag_source: Optional[str] = None,
        table: Optional[str] = None,
        version: Optional[str] = None,
    ) -> RespBody:
        """get tags. when tag_source is None, returns data from all tag sources by default"""
        table = table or self.table
        payload: Dict[str, Any] = {
            "table": table,
            "data_id": data_id,
        }
        if version:
            payload["version"] = version
        if tag_source:
            payload["tag_source"] = tag_source
        resp = self._webapp_client.do_request(
            WebappClient.POST, "/dataTags/detail", json=payload
        )

        return resp.to_resp_body()

from typing import Any, Dict, List, Optional, Tuple

from dm_sdk.models import RespBody
from dm_sdk.prod_data.base import ProdDataBasic
from dm_sdk.prod_data.models import (
    DATA_TYPE_BAG,
    DATA_TYPE_CLIP,
    DATA_TYPE_VIRTUAL_CLIP,
)
from dm_sdk.tools.api import ApiBaseError
from dm_sdk.tools.webapp_client import WebappClient
from dm_sdk.tools.type_check import checked


class MetadataMixin(ProdDataBasic):
    """元数据与标签 CRUD Mixin。"""

    def create_clip_metadata(
        self,
        origins: List[Tuple[str, str]],
        parents: List[Tuple[str, str]],
        start_timestamp: int,
        end_timestamp: int,
        duration: int,
        table: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        extend_data: Optional[Dict[str, Any]] = None,
        return_exist: Optional[bool] = False,
    ) -> RespBody:
        """Create metadata for a clip.

        Args:
            origins: List of origin (bag_id, table) tuples.
            parents: List of parent (data_id, table) tuples.
            table: Table name. Defaults to self.table.
            metadata: Extensible metadata dictionary.
            extend_data: Extensible data dictionary but not indexed.

        Returns:
            RespBody Object containing the created object metadata.
        """
        self._validate_id_list(origins, "origins")
        self._validate_id_list(parents, "parents")
        data_id, result = self._create_metadata(
            data_type=DATA_TYPE_CLIP,
            origins=origins,
            parents=parents,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
            duration=duration,
            table=table,
            metadata=metadata,
            extend_data=extend_data,
            return_exist=return_exist,
        )
        self._tracker.track(
            data_id, table or self.table, "create_clip_metadata"
        )
        return result

    def create_bag_metadata(
        self,
        origins: List[Tuple[str, str]],
        parents: List[Tuple[str, str]],
        start_timestamp: int,
        end_timestamp: int,
        duration: int,
        table: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        extend_data: Optional[Dict[str, Any]] = None,
        return_exist: Optional[bool] = False,
    ) -> RespBody:
        """Create metadata for a bag. Parameters same as create_clip_metadata."""
        self._validate_id_list(origins, "origins")
        self._validate_id_list(parents, "parents")
        data_id, result = self._create_metadata(
            data_type=DATA_TYPE_BAG,
            origins=origins,
            parents=parents,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
            duration=duration,
            table=table,
            metadata=metadata,
            extend_data=extend_data,
            return_exist=return_exist,
        )
        self._tracker.track(
            data_id, table or self.table, "create_bag_metadata"
        )
        return result

    def create_virtual_clip(
        self,
        origins: List[Tuple[str, str]],
        parents: List[Tuple[str, str]],
        start_timestamp: int,
        end_timestamp: int,
        duration: int,
        table: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        extend_data: Optional[Dict[str, Any]] = None,
        return_exist: Optional[bool] = False,
    ) -> RespBody:
        """Create metadata for a virtual clip. Parameters same as create_clip_metadata."""
        self._validate_id_list(origins, "origins")
        self._validate_id_list(parents, "parents")
        data_id, result = self._create_metadata(
            data_type=DATA_TYPE_VIRTUAL_CLIP,
            origins=origins,
            parents=parents,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
            duration=duration,
            table=table,
            metadata=metadata,
            extend_data=extend_data,
            return_exist=return_exist,
        )
        self._tracker.track(
            data_id, table or self.table, "create_virtual_clip"
        )
        return result

    def _create_metadata(
        self,
        data_type,
        origins,
        parents,
        start_timestamp: int,
        end_timestamp: int,
        duration: int,
        table=None,
        metadata=None,
        extend_data=None,
        return_exist=None,
    ) -> Tuple[str, RespBody]:
        table = table or self.table
        request_body = {
            "data_type": data_type,
            "origins": [{"bag_id": i[0], "table": i[1]} for i in origins],
            "parents": [{"data_id": i[0], "table": i[1]} for i in parents],
            "table": table,
            "start_timestamp": start_timestamp,
            "end_timestamp": end_timestamp,
            "duration": duration,
            "return_exist": return_exist,
        }
        if metadata:
            request_body["metadata"] = metadata
        if extend_data:
            request_body["extend_data"] = extend_data
        resp = self._webapp_client.do_request(
            WebappClient.POST, "/data/add", json=request_body
        )
        data = resp.get("data") or {}
        return data.get("data_id", ""), resp.to_resp_body()

    @checked
    def get_clip_metadata(
        self,
        data_id: str,
        table: Optional[str] = None,
        fields: Optional[List[str]] = None,
    ) -> RespBody:
        """Get clip metadata by data_id.

        Args:
            data_id: The clip ID.
            table: Table name. If not specified, uses the default table.
            fields: List of fields to return, if not specified returns all fields.

        Returns:
            RespBody Object containing the clip metadata.
        """
        data_id, result = self._get_metadata(data_id, table, fields)
        if result.code == 200:
            self._tracker.track(
                data_id, table or self.table, "get_clip_metadata"
            )
        return result

    @checked
    def get_bag_metadata(
        self,
        data_id: str,
        table: Optional[str] = None,
        fields: Optional[List[str]] = None,
    ) -> RespBody:
        """Get bag metadata by ID. Parameters same as get_clip_metadata."""
        data_id, result = self._get_metadata(data_id, table, fields)
        if result.code == 200:
            self._tracker.track(
                data_id, table or self.table, "get_bag_metadata"
            )
        return result

    @checked
    def update_clip_metadata(
        self,
        data_id: str,
        table: Optional[str] = None,
        origins=None,
        parents=None,
        metadata=None,
        metadata_replace=False,
        extend_data=None,
        start_timestamp: Optional[int] = None,
        end_timestamp: Optional[int] = None,
        duration: Optional[int] = None,
    ) -> RespBody:
        """Update metadata for a clip.
        Args:
            data_id: The clip ID.
            table: Table name. If not specified, uses the default table name.
            origins: List of origin table and bag_id tuples as (bag_id, table).
            parents: List of parent table and data_id tuples as (data_id, table).
            metadata: Extensible field metadata.
            metadata_replace: If True, metadata fields will be fully replaced.
            extend_data: Extensible field extend_data.
            tags_replace: If True, tags fields will be fully replaced.

        Returns:
            RespBody Object indicating success or failure.
        """
        result = self._update_metadata(
            data_id,
            table,
            origins,
            parents,
            metadata,
            metadata_replace,
            extend_data,
            start_timestamp,
            end_timestamp,
            duration,
        )
        if result.resp_code() == 200:
            self._tracker.track(
                data_id, table or self.table, "update_clip_metadata"
            )
        return result

    @checked
    def update_bag_metadata(
        self,
        data_id: str,
        table: Optional[str] = None,
        origins=None,
        parents=None,
        metadata=None,
        metadata_replace=False,
        extend_data=None,
        start_timestamp: Optional[int] = None,
        end_timestamp: Optional[int] = None,
        duration: Optional[int] = None,
    ) -> RespBody:
        """Update metadata for a bag. Parameters same as create_bag_metadata"""
        result = self._update_metadata(
            data_id,
            table,
            origins,
            parents,
            metadata,
            metadata_replace,
            extend_data,
            start_timestamp,
            end_timestamp,
            duration,
        )
        if result.resp_code() == 200:
            self._tracker.track(
                data_id, table or self.table, "update_bag_metadata"
            )
        return result

    def _update_metadata(
        self,
        data_id,
        table=None,
        origins=None,
        parents=None,
        metadata=None,
        metadata_replace=False,
        extend_data=None,
        start_timestamp: Optional[int] = None,
        end_timestamp: Optional[int] = None,
        duration: Optional[int] = None,
    ) -> RespBody:
        if origins:
            self._validate_id_list(origins, "origins")
        if parents:
            self._validate_id_list(parents, "parents")

        table = table or self.table
        request_body = {"data_id": data_id, "table": table}
        if origins:
            request_body["origins"] = [
                {"bag_id": i[0], "table": i[1]} for i in origins
            ]
        if parents:
            request_body["parents"] = [
                {"data_id": i[0], "table": i[1]} for i in parents
            ]
        if metadata:
            request_body["metadata"] = metadata
            if metadata_replace:
                request_body["metadata_replace"] = True
        if extend_data:
            request_body["extend_data"] = extend_data
        if start_timestamp is not None:
            request_body["start_timestamp"] = start_timestamp
        if end_timestamp is not None:
            request_body["end_timestamp"] = end_timestamp
        if duration is not None:
            request_body["duration"] = duration

        resp = self._webapp_client.do_request(
            WebappClient.POST, "/data/update", json=request_body
        )
        return resp.to_resp_body()

    def upsert_clip_metadata(
        self,
        origins: List[Tuple[str, str]],
        parents: List[Tuple[str, str]],
        start_timestamp: int,
        end_timestamp: int,
        duration: int,
        table: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        extend_data: Optional[Dict[str, Any]] = None,
    ) -> RespBody:
        """Upsert metadata for a clip.

        Args:
            origins: List of origin (bag_id, table) tuples.
            parents: List of parent (data_id, table) tuples.
            table: Table name. Defaults to self.table.
            metadata: Extensible metadata dictionary.
            extend_data: Extensible data dictionary but not indexed.

        Returns:
            RespBody Object containing the created object metadata.
        """
        self._validate_id_list(origins, "origins")
        self._validate_id_list(parents, "parents")
        result = self._upsert_metadata(
            data_type=DATA_TYPE_CLIP,
            origins=origins,
            parents=parents,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
            duration=duration,
            table=table,
            metadata=metadata,
            extend_data=extend_data,
        )
        if result.resp_data() and result.resp_data().get("data_id"):
            self._tracker.track(
                result.resp_data().get("data_id"),
                table or self.table,
                "upsert_clip_metadata",
            )
        return result

    def upsert_bag_metadata(
        self,
        origins: List[Tuple[str, str]],
        parents: List[Tuple[str, str]],
        start_timestamp: int,
        end_timestamp: int,
        duration: int,
        table: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        extend_data: Optional[Dict[str, Any]] = None,
    ) -> RespBody:
        """Upsert metadata for a bag. Parameters same as upsert_clip_metadata."""
        self._validate_id_list(origins, "origins")
        self._validate_id_list(parents, "parents")
        result = self._upsert_metadata(
            data_type=DATA_TYPE_BAG,
            origins=origins,
            parents=parents,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
            duration=duration,
            table=table,
            metadata=metadata,
            extend_data=extend_data,
        )
        if result.resp_data() and result.resp_data().get("data_id"):
            self._tracker.track(
                result.resp_data().get("data_id"),
                table or self.table,
                "upsert_bag_metadata",
            )
        return result

    def upsert_virtual_clip(
        self,
        origins: List[Tuple[str, str]],
        parents: List[Tuple[str, str]],
        start_timestamp: int,
        end_timestamp: int,
        duration: int,
        table: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        extend_data: Optional[Dict[str, Any]] = None,
    ) -> RespBody:
        """Upsert metadata for a virtual clip. Parameters same as upsert_clip_metadata."""
        self._validate_id_list(origins, "origins")
        self._validate_id_list(parents, "parents")
        result = self._upsert_metadata(
            data_type=DATA_TYPE_VIRTUAL_CLIP,
            origins=origins,
            parents=parents,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
            duration=duration,
            table=table,
            metadata=metadata,
            extend_data=extend_data,
        )
        if result.resp_data() and result.resp_data().get("data_id"):
            self._tracker.track(
                result.resp_data().get("data_id"),
                table or self.table,
                "upsert_virtual_clip",
            )
        return result

    def _upsert_metadata(
        self,
        data_type: str,
        origins: List[Tuple[str, str]],
        parents: List[Tuple[str, str]],
        start_timestamp: int,
        end_timestamp: int,
        duration: int,
        table: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        extend_data: Optional[Dict[str, Any]] = None,
    ) -> RespBody:
        table = table or self.table
        request_body = {
            "data_type": data_type,
            "origins": [{"bag_id": i[0], "table": i[1]} for i in origins],
            "parents": [{"data_id": i[0], "table": i[1]} for i in parents],
            "table": table,
            "start_timestamp": start_timestamp,
            "end_timestamp": end_timestamp,
            "duration": duration,
        }
        if metadata:
            request_body["metadata"] = metadata
        if extend_data:
            request_body["extend_data"] = extend_data

        resp = self._webapp_client.do_request(
            WebappClient.POST, "/data/upsert", json=request_body
        )
        return resp.to_resp_body(filter_fields=("data_id", "storage_prefix"))

    @staticmethod
    def _validate_id_list(id_list: List[Tuple[str, str]], param_name: str):
        for item in id_list:
            if (
                not isinstance(item, tuple)
                or len(item) != 2
                or not item[0]
                or not item[1]
            ):
                raise ApiBaseError(
                    f"{param_name}中的元素必须为(id, table)元组"
                )

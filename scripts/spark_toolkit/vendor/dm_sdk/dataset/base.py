from importlib.metadata import version
from typing import Optional

from dm_sdk.tools.dm_static_env import (
    DEFAULT_TIMEOUT_SECONDS,
    ENV_PROD,
    SERVICE_DATASET,
    get_dataset_oss_bucket_name,
)
from dm_sdk.tools.oss_sts_client_manager import OSSToolManager
from dm_sdk.tools.webapp_client import WebappClient


class DatasetBasic:
    def __init__(
        self,
        access_token: str,
        table: str,
        env: Optional[str] = None,
        *args,
        **kwargs,
    ):
        """
        Initialize DatasetClient

        Args:
            access_token: Authentication token
            env: Environment name (dev/uat/prod)，不传时默认为 prod
            timeout: Request timeout in seconds
        """
        kwargs["timeout"] = DEFAULT_TIMEOUT_SECONDS
        ua = f"dataset_sdk/{version('dm_sdk')}"
        self.table = table

        # env 默认 prod
        env = env or ENV_PROD

        # service_targets 按服务名分别配置 X-Service-Target
        service_targets = kwargs.pop("service_targets", {}) or {}

        self._webapp_client = WebappClient(
            env, SERVICE_DATASET,
            service_target=service_targets.get(SERVICE_DATASET),
            *args, **kwargs
        )
        self._webapp_client.headers["User-Agent"] = ua
        self._webapp_client.headers["Access-Token"] = access_token
        self._oss_tool = OSSToolManager(
            backend_sts_url="/common-oss/oss-info",
            webapp_client=self._webapp_client,
        )
        self._bucket_name = get_dataset_oss_bucket_name(env)

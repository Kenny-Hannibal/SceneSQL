#!/usr/bin/env python3
import logging
import time
import uuid
from typing import Literal, Optional

import requests

from dm_sdk.models import RespBody
from dm_sdk.tools.dm_static_env import get_host, ENV_DEV

_RETRY_TIMES_LIMIT = 10
_RETRY_INTERVAL_LIMIT = 20
_TIMEOUT_LIMIT = 2000
_DEFAULT_TIMEOUT = 20
_DEFAULT_MAX_RETRIES = 2
_DEFAULT_RETRY_INTERVAL = 5


class ResponseDict(dict):
    """携带追踪信息的响应字典，行为与普通 dict 完全一致。"""

    trace_id: str

    def __new__(
        cls,
        data: dict,
        trace_id: str = "",
    ):
        instance = super().__new__(cls, data)
        instance.trace_id = trace_id
        return instance

    def to_resp_body(self, data=None, filter_fields=None) -> RespBody:
        """转换为 RespBody，自动携带 trace_id。

        Args:
            data: 自定义 data，默认取 self["data"]。
            filter_fields: 可选字段列表，仅保留 data 中这些字段（仅 data 为 dict 时生效）。
        """
        if data is None:
            data = self.get("data")
        if filter_fields and isinstance(data, dict):
            data = {k: data[k] for k in filter_fields if k in data}
        return RespBody(
            code=self.get("code", 0),
            msg=self.get("message", ""),
            data=data,
            trace_id=self.trace_id,
        )


class WebappClientException(Exception):
    """HTTP 请求异常，携带状态码和 trace_id。"""

    def __init__(self, url, message, status_code, trace_id: str = ""):
        self.status_code = status_code
        self.trace_id = trace_id
        message = f"Error response from {url}\n{message}"
        if trace_id:
            message = f"{message} (trace_id: {trace_id})"
        super().__init__(message)


class WebappClient:
    """Webapp API 客户端。

    基于 requests.Session 实现连接池复用，内置单层重试和链路追踪。
    """

    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"
    PATCH = "PATCH"

    def __init__(
        self,
        env: str,
        service_name: str,
        service_target: Optional[str] = None,
        timeout: int = _DEFAULT_TIMEOUT,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        retry_interval: int = _DEFAULT_RETRY_INTERVAL,
    ):
        """
        :param env: 环境名称 (dev/uat/prod)，必填。
        :param service_name: 服务名称，用于解析 host，必填。
        :param service_target: 开发者路由标识，设置 X-Service-Target header。
        :param timeout: 请求超时（秒），上限 _TIMEOUT_LIMIT。
        :param max_retries: 最大重试次数（含首次请求），上限 _RETRY_TIMES_LIMIT。
        :param retry_interval: 重试间隔（秒），上限 _RETRY_INTERVAL_LIMIT。
        """
        self._logger = logging.getLogger(__name__)
        self.host = get_host(env, service_name)

        self._session = requests.Session()
        self.headers = {
            "Content-type": "application/json",
            "Accept": "application/json",
        }
        if env == ENV_DEV and service_target:
            self.headers["X-Service-Target"] = service_target

        self.timeout = min(timeout, _TIMEOUT_LIMIT)
        self.max_retries = min(max_retries, _RETRY_TIMES_LIMIT)
        self.retry_interval = min(retry_interval, _RETRY_INTERVAL_LIMIT)

    def do_request(
        self,
        method: Literal["GET", "POST", "PUT", "DELETE", "PATCH"],
        uri: str,
        *,
        max_retries: int = None,
        trace_id: str = "",
        **kwargs,
    ) -> ResponseDict:
        """发送 HTTP 请求并返回响应。

        Args:
            method: HTTP 方法 ("GET", "POST", "PUT", "DELETE", "PATCH")。
            uri: 请求路径（不含 host）。
            max_retries: 最大重试次数，默认使用实例配置。
            trace_id: 链路追踪 ID。若为空则自动生成；若提供则复用，便于与上游链路关联。
            **kwargs: 传递给 requests.Session.request() 的参数
                      (如 json=, params=, data= 等)。

        Returns:
            ResponseDict: 携带 trace_id/elapsed/status_code 的响应字典。

        Raises:
            WebappClientException: HTTP 4xx 错误（不重试）。
            RuntimeError: 所有重试耗尽。
        """
        if max_retries is None:
            max_retries = self.max_retries

        # trace_id 优先级：显式传入 > 自动生成
        if not trace_id:
            trace_id = uuid.uuid4().hex

        # 合并 headers：默认 headers + 用户自定义 + RequestId
        headers = dict(self.headers)
        if "headers" in kwargs:
            headers.update(kwargs.pop("headers"))
        headers["RequestId"] = trace_id
        kwargs["headers"] = headers

        # 默认 timeout
        if "timeout" not in kwargs:
            kwargs["timeout"] = self.timeout

        url = f"{self.host}{uri}"
        last_error = None

        for attempt in range(1, max_retries + 1):
            http_response = None
            start_time = time.monotonic()
            try:
                http_response = self._session.request(method, url, **kwargs)
                elapsed = time.monotonic() - start_time
                http_response.raise_for_status()
                result = http_response.json()

                self._logger.debug(
                    "%s %s => %s (%.3fs, trace_id: %s)",
                    method,
                    uri,
                    http_response.status_code,
                    elapsed,
                    trace_id,
                )

                return ResponseDict(
                    result,
                    trace_id=trace_id,
                )

            except requests.exceptions.HTTPError as e:
                elapsed = time.monotonic() - start_time
                resp_json = None
                status_code = 0

                if http_response is not None:
                    status_code = http_response.status_code
                    try:
                        resp_json = http_response.json()
                    except Exception:
                        pass

                # 4xx 客户端错误：不重试，直接抛出
                if 400 <= status_code < 500:
                    self._logger.warning(
                        "%s %s => %s (%.3fs, trace_id: %s): %s",
                        method,
                        uri,
                        status_code,
                        elapsed,
                        trace_id,
                        resp_json,
                    )
                    raise WebappClientException(
                        url,
                        str(resp_json or e),
                        status_code,
                        trace_id=trace_id,
                    ) from e

                # 5xx 服务端错误：重试
                last_error = e
                self._logger.warning(
                    "%s %s => %s (%.3fs, trace_id: %s), attempt %d/%d",
                    method,
                    uri,
                    status_code,
                    elapsed,
                    trace_id,
                    attempt,
                    max_retries,
                )

            except (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
            ) as e:
                elapsed = time.monotonic() - start_time
                last_error = e
                self._logger.warning(
                    "%s %s failed (%.3fs, trace_id: %s), attempt %d/%d: %s",
                    method,
                    uri,
                    elapsed,
                    trace_id,
                    attempt,
                    max_retries,
                    e,
                )

            except Exception as e:
                elapsed = time.monotonic() - start_time
                last_error = e
                self._logger.warning(
                    "%s %s error (%.3fs, trace_id: %s), attempt %d/%d: %s",
                    method,
                    uri,
                    elapsed,
                    trace_id,
                    attempt,
                    max_retries,
                    e,
                )

            # 重试间隔（最后一次不等待）
            if attempt < max_retries:
                time.sleep(self.retry_interval)

        # 所有重试耗尽
        raise RuntimeError(
            f"Request {method} {url} failed after {max_retries} attempts "
            f"(trace_id: {trace_id}): {last_error}"
        )

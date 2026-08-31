#!/usr/bin/env python3
from http import HTTPStatus


class ApiBaseError(Exception):
    """自定义 API 基础异常，携带 trace_id。"""

    def __init__(self, message: str = "", trace_id: str = ""):
        self.trace_id = trace_id
        if trace_id and "(trace_id:" not in message:
            message = f"{message} (trace_id: {trace_id})"
        super().__init__(message)


class InvalidUserInputError(ApiBaseError):
    """Class for invalid user input error."""

    http_status_code = HTTPStatus.BAD_REQUEST.value

    def __init__(self, message=None, **kwargs):
        self.message = message or "Invalid user input data"
        super().__init__(self.message, **kwargs)

    def get_message(self):
        return self.message

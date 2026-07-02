"""115 API 异常定义"""


class Agent115Error(Exception):
    """基础异常"""


class AuthError(Agent115Error):
    """认证相关错误：cookie 无效、未登录等"""


class APIError(Agent115Error):
    """115 API 返回的错误"""

    def __init__(self, message: str, errno: int = -1, response: dict | None = None):
        super().__init__(message)
        self.errno = errno
        self.response = response or {}


class NetworkError(Agent115Error):
    """网络请求错误"""


class ValidationError(Agent115Error):
    """参数校验错误"""

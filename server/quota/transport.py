"""Quota rejections without private balances or cost details in chat APIs."""

from server.quota.errors import QuotaErrorCode


def quota_error(error):
    code = error.code
    if code in {
        QuotaErrorCode.POLICY_NOT_FOUND,
        QuotaErrorCode.POLICY_AMBIGUOUS,
        QuotaErrorCode.ADMISSION_DENIED,
    }:
        return code.value, "额度配置暂不可用，请联系管理员", 503
    if code == QuotaErrorCode.CONCURRENCY_LIMIT:
        return code.value, "当前请求较多，请稍后再试", 429
    return code.value, "当前可用额度不足，暂时无法发送", 429

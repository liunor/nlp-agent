"""Verification-email policy shared by storage, API responses and SMTP text.

The deployed Tencent SES template also states five minutes. A future duration
change must update that externally managed template before release.
"""

EMAIL_CODE_TTL_S = 300
EMAIL_RESEND_COOLDOWN_S = 60

"""SMTP email helpers for registration flows."""
from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Optional

from .log_utils import log_event, log_exception


def _parse_bool(value: Optional[str], *, default: bool = True) -> bool:
    if value is None:
        return default
    value = value.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


@dataclass
class EmailConfig:
    host: str
    port: int
    username: Optional[str]
    password: Optional[str]
    from_address: str
    use_tls: bool
    timeout: float

    @property
    def enabled(self) -> bool:  # pragma: no cover - trivial getter
        return bool(self.host and self.from_address)


def _load_email_config() -> Optional[EmailConfig]:
    host = (os.getenv("SMTP_HOST") or os.getenv("SMTP_SERVER") or "").strip()
    if not host:
        return None

    port = int(os.getenv("SMTP_PORT", "587"))
    username = (
        os.getenv("SMTP_USERNAME")
        or os.getenv("SMTP_USER")
        or os.getenv("SMTP_LOGIN")
    )
    password = (
        os.getenv("SMTP_PASSWORD")
        or os.getenv("SMTP_PASS")
        or os.getenv("SMTP_SECRET")
    )
    from_address = (
        os.getenv("SMTP_FROM")
        or os.getenv("SMTP_SENDER")
        or (username or "")
    ).strip()

    use_tls = _parse_bool(os.getenv("SMTP_USE_TLS"), default=True)
    timeout = float(os.getenv("SMTP_TIMEOUT", "10"))

    if not from_address:
        return None

    if username and not password:
        log_event(
            "email.configuration.invalid",
            level="WARNING",
            reason="username_provided_without_password",
        )
        return None

    return EmailConfig(
        host=host,
        port=port,
        username=username.strip() if username else None,
        password=password.strip() if password else None,
        from_address=from_address,
        use_tls=use_tls,
        timeout=timeout,
    )


def send_verification_email(to_address: str, code: str, expires_at: float) -> bool:
    """Send a verification email if SMTP settings are configured."""

    config = _load_email_config()
    if config is None or not config.enabled:
        log_event(
            "email.disabled",
            level="WARNING",
            reason="missing_configuration",
            to=to_address,
        )
        return False

    subject = os.getenv("SMTP_VERIFICATION_SUBJECT", "Sado Trade Bot 인증 코드")
    expires_dt = datetime.fromtimestamp(expires_at, tz=timezone.utc)
    expires_str = expires_dt.strftime("%Y-%m-%d %H:%M:%S %Z")
    dashboard_title = os.getenv("DASHBOARD_TITLE", "Sado Trade Bot")

    body = (
        f"안녕하세요.\n\n"
        f"{dashboard_title} 가입 인증 코드는 {code} 입니다.\n"
        f"이 코드는 {expires_str} 까지 유효하며, 만료되면 새 코드를 요청해야 합니다.\n\n"
        "본 메일이 본인 요청이 아니라면 관리자에게 즉시 알려주세요."
    )

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.from_address
    message["To"] = to_address
    message.set_content(body)

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(config.host, config.port, timeout=config.timeout) as smtp:
            if config.use_tls:
                smtp.starttls(context=context)
            if config.username and config.password:
                smtp.login(config.username, config.password)
            smtp.send_message(message)
    except Exception as exc:  # pragma: no cover - network failure path
        log_exception(
            "email.send.failure",
            exc,
            to=to_address,
            host=config.host,
            port=config.port,
        )
        return False

    log_event(
        "email.send.success",
        to=to_address,
        host=config.host,
        port=config.port,
    )
    return True


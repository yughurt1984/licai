"""SMTP 邮件发送(账户中心: 注册验证码 / 绑定邮箱验证码)。

部署级全局配置(os.environ > config.env, 与 THS_API_KEY 等数据源凭证同一加载链路):
  SMTP_HOST       如 smtp.qq.com / smtp.gmail.com, 留空 = 邮件功能关闭
  SMTP_PORT       默认 465
  SMTP_USER       登录账号(多数服务商=发件邮箱)
  SMTP_PASS       授权码/应用专用密码(不是邮箱登录密码)
  SMTP_FROM       发件人显示地址, 留空用 SMTP_USER
  SMTP_TLS        ssl(默认, 465) | starttls(587) | none

同步 smtplib 放线程池跑(FastAPI 事件循环里不阻塞)。未配置时 send 返回 False,
调用方(auth.send_email_code)给用户明确文案。
"""
from __future__ import annotations

import asyncio
import logging
import os
import smtplib
import ssl
from email.mime.text import MIMEText
from email.utils import formataddr


def smtp_configured() -> bool:
    return bool((os.environ.get("SMTP_HOST") or "").strip())


def _smtp_from() -> str:
    return (os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER") or "").strip()


def _send_sync(to: str, subject: str, body: str) -> bool:
    host = (os.environ.get("SMTP_HOST") or "").strip()
    if not host:
        raise ValueError("SMTP 未配置")
    port = int((os.environ.get("SMTP_PORT") or "465").strip() or 465)
    user = (os.environ.get("SMTP_USER") or "").strip()
    password = (os.environ.get("SMTP_PASS") or "").strip()
    mode = (os.environ.get("SMTP_TLS") or "ssl").strip().lower()
    sender = _smtp_from()

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = formataddr(("理财助手", sender))
    msg["To"] = to

    if mode == "ssl":
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=20) as s:
            if user:
                s.login(user, password)
            s.sendmail(sender, [to], msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=20) as s:
            if mode == "starttls":
                s.starttls(context=ssl.create_default_context())
            if user:
                s.login(user, password)
            s.sendmail(sender, [to], msg.as_string())
    return True


async def send_email(to: str, subject: str, body: str) -> bool:
    """发一封纯文本邮件。SMTP 未配置抛 ValueError; 发送失败抛原异常(调用方转文案)。"""
    if not smtp_configured():
        raise ValueError("邮件服务未配置, 请在 config.env 填写 SMTP_HOST/SMTP_USER/SMTP_PASS 后重启")
    return await asyncio.to_thread(_send_sync, to, subject, body)


async def send_verification_code(to: str, code: str, purpose: str) -> bool:
    """验证码邮件。purpose: register=注册 | bind=绑定邮箱。"""
    action = "注册" if purpose == "register" else "绑定"
    subject = f"理财助手{action}验证码: {code}"
    body = (
        f"你好！\n\n"
        f"你在理财助手进行「{action}」操作的验证码是:\n\n"
        f"    {code}\n\n"
        f"10 分钟内有效。如果不是本人操作, 请忽略本邮件。\n"
    )
    ok = await send_email(to, subject, body)
    if ok:
        logging.info("[mailer] 验证码已发往 %s (purpose=%s)", to, purpose)
    return ok

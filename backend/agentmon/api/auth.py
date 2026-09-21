"""Web autentifikatsiya: AD (LDAP bind + guruh a'zoligi) yoki favqulodda lokal admin.

Sessiya — imzolangan HttpOnly cookie (server tomonida holat saqlanmaydi).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import secrets
import ssl
import sys
import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, URLSafeTimedSerializer
from ldap3 import SIMPLE, SUBTREE, Connection, Server, Tls
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars

from agentmon.config import Settings, get_settings

COOKIE = "agentmon_session"
PBKDF2_ITER = 390_000
_attempts: dict[str, deque] = defaultdict(deque)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITER)
    # Ajratuvchi ':' — '$' docker compose .env faylida o'zgaruvchi sifatida talqin qilinadi va hash buziladi.
    return f"pbkdf2_sha256:{PBKDF2_ITER}:{base64.b64encode(salt).decode()}:{base64.b64encode(dk).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iters, salt, digest = encoded.replace("$", ":").split(":")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(iters))
        return hmac.compare_digest(dk, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def _serializer(s: Settings) -> URLSafeTimedSerializer:
    if not s.web_secret or len(s.web_secret) < 32:
        raise RuntimeError("WEB_SECRET kamida 32 belgidan iborat bo'lishi kerak (openssl rand -hex 32)")
    return URLSafeTimedSerializer(s.web_secret, salt="agentmon-session")


def check_config(s: Settings) -> None:
    if s.web_auth not in ("ldap", "none"):
        raise RuntimeError(f"WEB_AUTH noto'g'ri: {s.web_auth!r} (ldap | none)")
    if s.web_auth == "ldap":
        _serializer(s)
        if not (s.ad_server and s.ad_base_dn) and not (s.web_admin_user and s.web_admin_password_hash):
            raise RuntimeError("WEB_AUTH=ldap uchun AD_SERVER/AD_BASE_DN yoki WEB_ADMIN_* sozlanishi kerak")


def issue_cookie(s: Settings, user: str) -> str:
    return _serializer(s).dumps({"u": user})


def rate_limited(key: str, limit: int = 5, window: int = 60) -> bool:
    q = _attempts[key]
    now = time.monotonic()
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


def _domain_from_base(base_dn: str) -> str:
    return ".".join(p.split("=", 1)[1] for p in base_dn.split(",") if p.strip().upper().startswith("DC="))


def _ldap_check(s: Settings, username: str, password: str) -> str | None:
    """Foydalanuvchi nomi bilan bind qiladi va ruxsat berilgan guruhga (ichma-ich ham) a'zoligini tekshiradi."""
    sam = username.split("\\")[-1].split("@")[0]
    principal = username if ("\\" in username or "@" in username) else f"{sam}@{_domain_from_base(s.ad_base_dn)}"
    tls = Tls(validate=ssl.CERT_REQUIRED if s.ad_verify_tls else ssl.CERT_NONE)
    server = Server(s.ad_server, use_ssl=s.ad_server.lower().startswith("ldaps"), tls=tls, connect_timeout=10)
    try:
        conn = Connection(server, user=principal, password=password, authentication=SIMPLE, read_only=True, auto_referrals=False)
        if not conn.bind():
            return None
        try:
            flt = f"(&(objectCategory=person)(sAMAccountName={escape_filter_chars(sam)})"
            if s.web_allowed_group:
                flt += f"(memberOf:1.2.840.113556.1.4.1941:={escape_filter_chars(s.web_allowed_group)})"
            flt += ")"
            conn.search(s.ad_base_dn, flt, search_scope=SUBTREE, attributes=["sAMAccountName"], size_limit=1)
            return sam if conn.entries else None
        finally:
            conn.unbind()
    except LDAPException:
        return None


async def authenticate(s: Settings, username: str, password: str) -> str | None:
    # Bo'sh parol bilan LDAP "anonymous bind" muvaffaqiyatli bo'ladi — shuning uchun rad etiladi.
    if not username or not password:
        return None
    if s.web_admin_user and s.web_admin_password_hash and hmac.compare_digest(username, s.web_admin_user):
        return username if verify_password(password, s.web_admin_password_hash) else None
    if s.web_auth == "ldap" and s.ad_server:
        return await asyncio.to_thread(_ldap_check, s, username, password)
    return None


def current_user(request: Request, s: Settings = Depends(get_settings)) -> str:
    if s.web_auth == "none":
        return "anonymous"
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Kirish talab qilinadi")
    try:
        data = _serializer(s).loads(token, max_age=s.web_session_hours * 3600)
    except BadSignature:
        raise HTTPException(401, "Sessiya muddati tugagan") from None
    return data["u"]


if __name__ == "__main__":
    if sys.argv[1:] == ["hash"]:
        import getpass

        print(hash_password(getpass.getpass("Parol: ")))
    else:
        print("Foydalanish: python -m agentmon.api.auth hash")

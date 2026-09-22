"""Web autentifikatsiya: AD (LDAP bind + guruh a'zoligi) yoki favqulodda lokal admin.

Sessiya — imzolangan HttpOnly cookie (server tomonida holat saqlanmaydi).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import secrets
import sys
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, URLSafeTimedSerializer
from ldap3 import BASE, SIMPLE, SUBTREE, Connection, Server
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars

from agentmon.config import KNOWN_SAMPLE_SECRETS, Settings, get_settings, ldap_tls

log = logging.getLogger("agentmon.auth")
COOKIE = "agentmon_session"
ADMIN, VIEWER = "admin", "viewer"
_NESTED_MEMBER = "memberOf:1.2.840.113556.1.4.1941:="   # ichma-ich guruh a'zoligi (LDAP_MATCHING_RULE_IN_CHAIN)
PBKDF2_ITER = 390_000


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
    if s.web_secret in KNOWN_SAMPLE_SECRETS:
        raise RuntimeError("WEB_SECRET namunadan ko'chirilgan — o'zingiznikini yarating: openssl rand -hex 32")
    return URLSafeTimedSerializer(s.web_secret, salt="agentmon-session")


def check_config(s: Settings) -> None:
    s.products  # noqa: B018 — PRODUCTS_ENABLED noto'g'ri bo'lsa shu yerda xato beradi
    if s.web_auth not in ("ldap", "none"):
        raise RuntimeError(f"WEB_AUTH noto'g'ri: {s.web_auth!r} (ldap | none)")
    if any(sample in s.database_url for sample in KNOWN_SAMPLE_SECRETS):
        log.warning("POSTGRES_PASSWORD namunadan ko'chirilgan — almashtiring (docs: 'Parolni almashtirish')")
    if s.ad_server:
        ldap_tls(s)   # CA fayli noto'g'ri bo'lsa — ishga tushishda aniq xato
    if s.ad_server and not s.ad_verify_tls:
        log.warning("AD_VERIFY_TLS=false — DC sertifikati tekshirilmaydi, AD parollari MITM'ga ochiq")
    if s.web_auth == "ldap":
        _serializer(s)
        if not (s.ad_server and s.ad_base_dn) and not (s.web_admin_user and s.web_admin_password_hash):
            raise RuntimeError("WEB_AUTH=ldap uchun AD_SERVER/AD_BASE_DN yoki WEB_ADMIN_* sozlanishi kerak")
        if s.ad_server and not (s.ad_user and s.ad_password):
            raise RuntimeError("WEB_AUTH=ldap: AD orqali kirish uchun AD_USER/AD_PASSWORD (servis hisobi) kerak")


@dataclass(frozen=True, slots=True)
class Session:
    user: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == ADMIN


def issue_cookie(s: Settings, session: Session) -> str:
    return _serializer(s).dumps({"u": session.user, "r": session.role})


# Login urinishlarini cheklash (faqat muvaffaqiyatsizlari sanaladi, PostgreSQL'da — barcha worker'lar uchun umumiy).
# Kalitda doim mijoz IP'si bor: boshqa manzildan qilingan urinishlar haqiqiy adminni bloklay olmaydi.
FAIL_PER_USER = (5, 300)      # bitta IP'dan bitta login uchun: 5 daqiqada 5 ta
FAIL_PER_IP = (20, 900)       # bitta IP'dan jami (login nomlarini ketma-ket sinash): 15 daqiqada 20 ta


async def login_blocked(pool, ip: str, username: str) -> bool:
    row = await pool.fetchrow(
        """SELECT count(*) FILTER (WHERE username = $2 AND ts > now() - make_interval(secs => $3)) AS per_user,
                  count(*) AS per_ip
           FROM login_failure WHERE ip = $1 AND ts > now() - make_interval(secs => $4)""",
        ip, username.lower(), FAIL_PER_USER[1], FAIL_PER_IP[1])
    return row["per_user"] >= FAIL_PER_USER[0] or row["per_ip"] >= FAIL_PER_IP[0]


async def record_failure(pool, ip: str, username: str) -> None:
    await pool.execute("INSERT INTO login_failure (ip, username) VALUES ($1, $2)", ip, username.lower()[:256])
    await pool.execute("DELETE FROM login_failure WHERE ts < now() - interval '1 day'")


async def clear_failures(pool, ip: str, username: str) -> None:
    await pool.execute("DELETE FROM login_failure WHERE ip = $1 AND username = $2", ip, username.lower())


def _connection(s: Settings, user: str, password: str) -> Connection:
    """Bog'lanmagan (bind qilinmagan) LDAP ulanish. Testlarda almashtiriladi."""
    tls = ldap_tls(s)
    server = Server(s.ad_server, use_ssl=s.ad_server.lower().startswith("ldaps"), tls=tls, connect_timeout=10)
    return Connection(server, user=user, password=password, authentication=SIMPLE, read_only=True,
                      auto_referrals=False, receive_timeout=15)


def _user_filters(username: str) -> list[str]:
    """Kiritilgan login bo'yicha qidiruv filtrlari (tartib bo'yicha sinanadi). Faqat o'z domenimizda qidiriladi."""
    def person(attr: str, value: str) -> str:
        return f"(&(objectCategory=person)(objectClass=user)({attr}={escape_filter_chars(value)}))"

    if "\\" in username:                           # CORP\john
        return [person("sAMAccountName", username.rsplit("\\", 1)[1])]
    if "@" in username:                            # john@corp.local: avval UPN, bo'lmasa login qismi
        return [person("userPrincipalName", username), person("sAMAccountName", username.split("@", 1)[0])]
    return [person("sAMAccountName", username)]


def _ldap_check(s: Settings, username: str, password: str) -> Session | None:
    """"Avval qidirish, keyin bind": servis hisobi foydalanuvchini o'z domenimizda (va ruxsat berilgan guruhda,
    ichma-ich ham) qidiradi, so'ng aynan topilgan DN kiritilgan parol bilan tekshiriladi.

    Shunda ruxsat tekshirilgan hisob va parol tekshirilgan hisob doim bitta: boshqa (ishonchli) domendagi
    bir xil nomli foydalanuvchi o'z paroli bilan bu yerdagi hamnomining huquqini ololmaydi.
    """
    if not (s.ad_user and s.ad_password):
        log.error("LDAP login uchun AD_USER/AD_PASSWORD (servis hisobi) kerak")
        return None
    group = f"({_NESTED_MEMBER}{escape_filter_chars(s.web_allowed_group)})" if s.web_allowed_group else ""
    try:
        svc = _connection(s, s.ad_user, s.ad_password)
        if not svc.bind():
            log.error("LDAP servis hisobi bilan bog'lanib bo'lmadi: %s", svc.result.get("description"))
            return None
        try:
            entries = []
            for flt in _user_filters(username):
                if group:
                    flt = f"(&{flt}{group})"
                svc.search(s.ad_base_dn, flt, search_scope=SUBTREE, attributes=["sAMAccountName"], size_limit=2)
                entries = [e for e in svc.response or [] if e.get("type") == "searchResEntry"]
                if entries:
                    break
            if len(entries) != 1:
                return None   # topilmadi, guruhda emas yoki bir ma'noli emas
            dn = entries[0]["dn"]
            role = ADMIN
            if s.web_admin_group:
                svc.search(dn, f"(&(objectClass=user)({_NESTED_MEMBER}{escape_filter_chars(s.web_admin_group)}))",
                           search_scope=BASE, attributes=["cn"])
                is_admin = any(e.get("type") == "searchResEntry" for e in svc.response or [])
                role = ADMIN if is_admin else VIEWER
        finally:
            svc.unbind()
        sam = (entries[0].get("attributes") or {}).get("sAMAccountName")
        sam = sam[0] if isinstance(sam, list) else sam
        user = _connection(s, dn, password)
        try:
            if not user.bind():
                return None
        finally:
            user.unbind()
        return Session(sam or dn, role)
    except LDAPException:
        log.exception("LDAP xatosi")
        return None


async def authenticate(s: Settings, username: str, password: str) -> Session | None:
    # Bo'sh parol bilan LDAP "anonymous bind" muvaffaqiyatli bo'ladi — shuning uchun rad etiladi.
    if not username or not password:
        return None
    if s.web_admin_user and s.web_admin_password_hash and hmac.compare_digest(username, s.web_admin_user):
        return Session(username, ADMIN) if verify_password(password, s.web_admin_password_hash) else None
    if s.web_auth == "ldap" and s.ad_server:
        return await asyncio.to_thread(_ldap_check, s, username, password)
    return None


def current_session(request: Request, s: Settings = Depends(get_settings)) -> Session:
    if s.web_auth == "none":
        return Session("anonymous", ADMIN)
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Kirish talab qilinadi")
    try:
        data = _serializer(s).loads(token, max_age=s.web_session_hours * 3600)
    except BadSignature:
        raise HTTPException(401, "Sessiya muddati tugagan") from None
    # Rolsiz eski cookie — faqat ko'rish (qayta kirilganda rol aniqlanadi).
    return Session(data["u"], data.get("r", VIEWER))


def current_user(session: Session = Depends(current_session)) -> str:
    return session.user


def require_admin(session: Session = Depends(current_session)) -> Session:
    if not session.is_admin:
        raise HTTPException(403, "Bu amal uchun huquq yo'q (faqat administratorlar)")
    return session


if __name__ == "__main__":
    if sys.argv[1:] == ["hash"]:
        import getpass

        print(hash_password(getpass.getpass("Parol: ")))
    else:
        print("Foydalanish: python -m agentmon.api.auth hash")

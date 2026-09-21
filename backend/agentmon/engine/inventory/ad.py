"""Active Directory: kompyuter hisoblari, DC'lar, sayt subnetlari va AD DNS yozuvlari.

ldap3 sinxron, shuning uchun engine uni alohida thread'da chaqiradi.
Schema yuklanmaydi (tezroq) — atributlar raw ko'rinishda o'qiladi.
"""

from __future__ import annotations

import logging
import socket
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ldap3 import BASE, SIMPLE, SUBTREE, Connection, Server, Tls
from ldap3.core.exceptions import LDAPNoSuchObjectResult

from agentmon.config import Settings
from agentmon.engine.inventory.adns import parse_a_record
from agentmon.model import ConsoleRecord, norm_host

log = logging.getLogger(__name__)

UAC_DISABLED = 0x2
UAC_SERVER_TRUST = 0x2000  # domain controller
_EPOCH_1601 = datetime(1601, 1, 1, tzinfo=timezone.utc)


@dataclass
class DnsA:
    ip: str
    name: str
    fqdn: str
    observed_at: datetime | None


@dataclass
class ADInventory:
    computers: list[ConsoleRecord] = field(default_factory=list)
    dcs: list[tuple[str, str]] = field(default_factory=list)          # (ip, fqdn)
    subnets: list[tuple[str, str]] = field(default_factory=list)      # (cidr, site)
    dns: list[DnsA] = field(default_factory=list)


def filetime(raw: bytes | None) -> datetime | None:
    if not raw:
        return None
    v = int(raw)
    if v <= 0 or v >= 0x7FFFFFFFFFFFFFFF:
        return None
    return _EPOCH_1601 + timedelta(microseconds=v // 10)


def _s(raw: list[bytes] | None) -> str | None:
    return raw[0].decode("utf-8", "replace") if raw else None


def _rdn_value(dn: str | None) -> str:
    """'CN=Tashkent-HQ,CN=Sites,...' -> 'Tashkent-HQ'"""
    if not dn:
        return ""
    first = dn.split(",", 1)[0]
    return first.split("=", 1)[1] if "=" in first else first


class ADClient:
    def __init__(self, s: Settings) -> None:
        self.s = s

    def _connect(self) -> Connection:
        tls = Tls(validate=ssl.CERT_REQUIRED if self.s.ad_verify_tls else ssl.CERT_NONE)
        server = Server(self.s.ad_server, use_ssl=self.s.ad_server.lower().startswith("ldaps"),
                        tls=tls, get_info=None, connect_timeout=10)
        # AD simple bind'da 'DOMAIN\user', UPN yoki DN qabul qilinadi. LDAPS tavsiya etiladi.
        return Connection(server, user=self.s.ad_user, password=self.s.ad_password,
                          authentication=SIMPLE, auto_bind=True, read_only=True, auto_referrals=False, receive_timeout=120)

    def _paged(self, conn: Connection, base: str, flt: str, attrs: list[str]):
        gen = conn.extend.standard.paged_search(base, flt, search_scope=SUBTREE, attributes=attrs,
                                                paged_size=1000, generator=True)
        for entry in gen:
            if entry.get("type") == "searchResEntry":
                yield entry["dn"], entry["raw_attributes"]

    def fetch(self, now: datetime) -> ADInventory:
        s = self.s
        inv = ADInventory()
        conn = self._connect()
        try:
            conn.search("", "(objectClass=*)", search_scope=BASE,
                        attributes=["configurationNamingContext", "rootDomainNamingContext"])
            root = conn.response[0]["raw_attributes"] if conn.response else {}
            config_nc = _s(root.get("configurationNamingContext")) or f"CN=Configuration,{s.ad_base_dn}"
            forest_nc = _s(root.get("rootDomainNamingContext")) or s.ad_base_dn

            inv.dns = self._dns(conn, forest_nc) if s.ad_dns_zone else []
            inv.computers, dc_names = self._computers(conn, now)
            inv.subnets = self._subnets(conn, config_nc)
        finally:
            conn.unbind()

        by_fqdn: dict[str, list[str]] = {}
        for r in inv.dns:
            by_fqdn.setdefault(r.fqdn, []).append(r.ip)
        for fqdn in dc_names:
            ips = by_fqdn.get(fqdn) or _resolve(fqdn)
            inv.dcs.extend((ip, fqdn) for ip in ips)
        return inv

    def _computers(self, conn: Connection, now: datetime) -> tuple[list[ConsoleRecord], list[str]]:
        attrs = ["cn", "dNSHostName", "operatingSystem", "operatingSystemVersion",
                 "userAccountControl", "lastLogonTimestamp", "whenCreated"]
        stale_before = now - timedelta(days=self.s.ad_stale_days)
        out: list[ConsoleRecord] = []
        dcs: list[str] = []
        for dn, a in self._paged(conn, self.s.ad_base_dn, "(objectCategory=computer)", attrs):
            cn = _s(a.get("cn"))
            name = norm_host(cn)
            if not name:
                continue
            uac = int(_s(a.get("userAccountControl")) or 0)
            fqdn = (_s(a.get("dNSHostName")) or "").lower() or None
            if uac & UAC_SERVER_TRUST and fqdn:
                dcs.append(fqdn)
            ll = a.get("lastLogonTimestamp")
            last_logon = filetime(ll[0] if ll else None)
            enabled = not uac & UAC_DISABLED
            stale = last_logon is None or last_logon < stale_before
            reason = None if enabled else "AD'da kompyuter hisobi o'chirilgan (disabled)"
            out.append(ConsoleRecord(
                product="ad", name=name, display_name=cn or name, healthy=enabled, reason=reason,
                fqdn=fqdn, os=_s(a.get("operatingSystem")), last_seen=last_logon,
                version=_s(a.get("operatingSystemVersion")),
                details={"dn": dn, "enabled": enabled, "stale": stale,
                         "last_logon": last_logon.isoformat() if last_logon else None},
            ))
        return out, dcs

    def _subnets(self, conn: Connection, config_nc: str) -> list[tuple[str, str]]:
        base = f"CN=Subnets,CN=Sites,{config_nc}"
        try:
            return [(_s(a.get("cn")), _rdn_value(_s(a.get("siteObject"))))
                    for _, a in self._paged(conn, base, "(objectClass=subnet)", ["cn", "siteObject"])
                    if a.get("cn")]
        except LDAPNoSuchObjectResult:
            return []

    def _dns(self, conn: Connection, forest_nc: str) -> list[DnsA]:
        zone = self.s.ad_dns_zone.lower().rstrip(".")
        bases = [f"DC=DomainDnsZones,{self.s.ad_base_dn}", f"DC=ForestDnsZones,{forest_nc}",
                 f"CN=MicrosoftDNS,CN=System,{self.s.ad_base_dn}"]
        out: list[DnsA] = []
        for base in bases:
            try:
                conn.search(base, f"(&(objectClass=dnsZone)(name={zone}))", search_scope=SUBTREE, attributes=["name"])
            except LDAPNoSuchObjectResult:
                continue
            zones = [e["dn"] for e in (conn.response or []) if e.get("type") == "searchResEntry"]
            for zone_dn in zones:
                for _, a in self._paged(conn, zone_dn, "(objectClass=dnsNode)", ["name", "dnsRecord", "dNSTombstoned"]):
                    node = _s(a.get("name")) or ""
                    if node in ("@",) or node.startswith("_") or node.lower().endswith("dnszones"):
                        continue
                    if (_s(a.get("dNSTombstoned")) or "").upper() == "TRUE":
                        continue
                    fqdn = f"{node}.{zone}".lower()
                    for blob in a.get("dnsRecord", []):
                        rec = parse_a_record(blob)
                        if rec:
                            out.append(DnsA(rec[0], norm_host(node) or node.lower(), fqdn, rec[1]))
            if out:
                break
        return out


def _resolve(fqdn: str) -> list[str]:
    try:
        return list(dict.fromkeys(socket.gethostbyname_ex(fqdn)[2]))
    except OSError:
        return []

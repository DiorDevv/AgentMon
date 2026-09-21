from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# Tekshiriladigan mahsulotlar. "ad" uchun tarmoq signali — DC'larga trafik.
PRODUCTS: tuple[str, ...] = ("ad", "cortex", "ksc", "si")


def split_csv(value: str) -> list[str]:
    return [p.strip() for p in value.split(",") if p.strip()]


def parse_endpoints(value: str) -> list[tuple[str, int]]:
    """'1.2.3.4:80,5.6.7.8:443' -> [('1.2.3.4', 80), ('5.6.7.8', 443)]"""
    out = []
    for item in split_csv(value):
        ip, _, port = item.rpartition(":")
        out.append((ip, int(port)))
    return out


def parse_subnets(value: str) -> list[tuple[str, str]]:
    """'10.0.0.0/16=Markaz,10.1.0.0/16' -> [('10.0.0.0/16', 'Markaz'), ('10.1.0.0/16', '')]"""
    out = []
    for item in split_csv(value):
        cidr, _, site = item.partition("=")
        out.append((cidr.strip(), site.strip()))
    return out


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://agentmon:agentmon@localhost:5432/agentmon"
    redis_url: str = "redis://localhost:6379/0"
    redis_key: str = "nsel"

    target_cortex: str = "172.25.44.50:8888"
    target_ksc: str = "172.25.25.111:13000,172.25.25.111:14000"
    target_si: str = "172.25.43.205:8090"
    dc_ips: str = ""
    dc_ports: str = "88,389,445"
    user_subnets: str = ""
    exclude_subnets: str = ""
    exclude_ips: str = ""

    alive_window: int = 600
    grace: int = 1800
    thresh_ad: int = 14400
    thresh_cortex: int = 1800
    thresh_ksc: int = 2700
    thresh_si: int = 3600
    ksc_bases_max_age_hours: int = 72
    mass_outage_ratio: float = 0.2
    mass_outage_min: int = 20
    collector_stale: int = 180
    debounce: int = 2
    include_servers: bool = False

    flush_interval: int = 30
    eval_interval: int = 60
    inventory_interval: int = 900
    # Inventar manbasi shuncha vaqt yangilanmasa, uning ma'lumotiga tayanilmaydi.
    source_max_age: int = 4 * 3600
    # IP->host moslash uchun agent/DNS dalilining maksimal yoshi.
    identity_max_age: int = 14 * 86400

    ad_server: str = ""
    ad_base_dn: str = ""
    ad_user: str = ""
    ad_password: str = ""
    ad_verify_tls: bool = False
    ad_dns_zone: str = ""
    ad_stale_days: int = 45

    cortex_fqdn: str = ""
    cortex_key_id: str = ""
    cortex_key: str = ""
    cortex_key_type: str = "standard"

    ksc_url: str = ""
    ksc_user: str = ""
    ksc_password: str = ""
    ksc_domain: str = ""
    ksc_internal_user: bool = True
    ksc_verify_tls: bool = False
    ksc_filter: str = "(KLHST_WKS_FROM_UNASSIGNED = 0)"

    web_secret: str = ""
    web_auth: str = "ldap"
    web_allowed_group: str = ""
    web_admin_user: str = ""
    web_admin_password_hash: str = ""
    web_session_hours: int = 12
    web_cookie_secure: bool = False   # HTTPS orqali ochilsa true qiling

    @property
    def thresholds(self) -> dict[str, int]:
        return {
            "ad": self.thresh_ad,
            "cortex": self.thresh_cortex,
            "ksc": self.thresh_ksc,
            "si": self.thresh_si,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()

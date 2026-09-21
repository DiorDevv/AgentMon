"""Flow'ni tasniflash: manba IP qaysi saytga tegishli va manzil qaysi signal guruhiga kiradi."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from agentmon.config import Settings, parse_endpoints, parse_subnets, split_csv

_PRIVATE = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
UNKNOWN_SITE = "Noma'lum"
_CACHE_LIMIT = 200_000


@dataclass(frozen=True, slots=True)
class _Net:
    base: int
    mask: int
    prefix: int
    site: str


def _net(cidr: str, site: str = "") -> _Net:
    n = ipaddress.ip_network(cidr, strict=False)
    return _Net(int(n.network_address), int(n.netmask), n.prefixlen, site)


class Classifier:
    """O'zgarmas (immutable) obyekt. Konfiguratsiya o'zgarsa yangisi yaratiladi va almashtiriladi."""

    def __init__(
        self,
        targets: dict[tuple[str, int], str],
        dc_ips: set[str],
        dc_ports: set[int],
        user_subnets: list[tuple[str, str]],
        exclude_subnets: list[str],
        exclude_ips: set[str],
    ) -> None:
        self._targets = dict(targets)
        self._dc_ips = frozenset(dc_ips)
        self._dc_ports = frozenset(dc_ports)
        # Eng uzun prefiks birinchi — aniqroq subnet ustun.
        self._user = sorted((_net(c, s) for c, s in user_subnets), key=lambda n: -n.prefix)
        self._exclude = [_net(c) for c in exclude_subnets]
        server_ips = {ip for ip, _ in targets} | set(dc_ips)
        self._exclude_ips = frozenset(exclude_ips) | frozenset(server_ips)
        self._site_cache: dict[str, str | None] = {}

    @classmethod
    def from_settings(
        cls,
        s: Settings,
        ad_dc_ips: set[str] | None = None,
        ad_subnets: list[tuple[str, str]] | None = None,
    ) -> "Classifier":
        targets: dict[tuple[str, int], str] = {}
        for grp, raw in (("cortex", s.target_cortex), ("ksc", s.target_ksc), ("si", s.target_si)):
            for ep in parse_endpoints(raw):
                targets[ep] = grp
        dc_ips = set(split_csv(s.dc_ips)) | (ad_dc_ips or set())
        # Konfiguratsiyadagi subnetlar AD'dagidan ustun (bir xil CIDR bo'lsa).
        subnets = {c: site for c, site in (ad_subnets or [])}
        subnets.update({c: site or subnets.get(c, "") for c, site in parse_subnets(s.user_subnets)})
        return cls(
            targets=targets,
            dc_ips=dc_ips,
            dc_ports={int(p) for p in split_csv(s.dc_ports)},
            user_subnets=list(subnets.items()),
            exclude_subnets=split_csv(s.exclude_subnets),
            exclude_ips=set(split_csv(s.exclude_ips)),
        )

    @property
    def dc_ips(self) -> frozenset[str]:
        return self._dc_ips

    def target_group(self, dst: str, dport: int) -> str | None:
        grp = self._targets.get((dst, dport))
        if grp:
            return grp
        if dst in self._dc_ips and dport in self._dc_ports:
            return "ad"
        return None

    def site_of(self, src: str) -> str | None:
        """Foydalanuvchi hosti bo'lsa sayt nomi, aks holda None (flow hisobga olinmaydi)."""
        try:
            return self._site_cache[src]
        except KeyError:
            pass
        site = self._resolve_site(src)
        if len(self._site_cache) >= _CACHE_LIMIT:
            self._site_cache.clear()
        self._site_cache[src] = site
        return site

    def _resolve_site(self, src: str) -> str | None:
        if src in self._exclude_ips:
            return None
        try:
            ip = int(ipaddress.IPv4Address(src))
        except ValueError:
            return None
        for n in self._exclude:
            if ip & n.mask == n.base:
                return None
        if self._user:
            for n in self._user:
                if ip & n.mask == n.base:
                    return n.site or UNKNOWN_SITE
            return None
        # Subnetlar umuman ma'lum bo'lmasa: barcha xususiy manzillar.
        for n in _PRIVATE:
            if ip & int(n.netmask) == int(n.network_address):
                return UNKNOWN_SITE
        return None

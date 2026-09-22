#!/bin/sh
# AgentMon: Docker e'lon qilgan portlarga kirishni cheklash.
#
# MUHIM: Docker e'lon qilgan portlar (ports:) ufw qoidalarini CHETLAB o'tadi — "ufw deny" ularni yopmaydi.
# Docker trafikni to'g'ridan-to'g'ri o'z iptables zanjirlariga yo'naltiradi; administrator qoidalari uchun
# yagona to'g'ri joy — DOCKER-USER zanjiri. Bu skript shu zanjirga AGENTMON qoidalarini qo'yadi:
#   - NetFlow (UDP NSEL_PORT): faqat NSEL_EXPORTERS (FTD) manzillaridan;
#   - Web (WEB_HTTPS_PORT, WEB_PORT): WEB_ALLOWED_NETS berilgan bo'lsa — faqat shu tarmoqlardan.
#
# Foydalanish (loyiha papkasidan):
#   sudo ./deploy/docker-firewall.sh            # qoidalarni qo'yish/yangilash (.env dan o'qiydi, qayta ishga tushirsa bo'ladi)
#   sudo ./deploy/docker-firewall.sh --show     # joriy qoidalar
#   sudo ./deploy/docker-firewall.sh --remove   # qoidalarni olib tashlash
# Qayta yuklanishdan keyin ham ishlashi uchun: deploy/agentmon-firewall.service (docs/ISHGA-TUSHIRISH.md, 1.4).
set -eu

CHAIN=AGENTMON
ENV_FILE=${ENV_FILE:-$(cd "$(dirname "$0")/.." && pwd)/.env}

# Qiymat: avval muhit o'zgaruvchisi, keyin .env fayli, keyin standart qiymat.
get() {
    name=$1 default=$2
    eval "val=\${$name:-}"
    if [ -z "$val" ] && [ -f "$ENV_FILE" ]; then
        val=$(sed -n "s/^[[:space:]]*$name=//p" "$ENV_FILE" | tail -n 1 | sed "s/^['\"]//; s/['\"][[:space:]]*\$//")
    fi
    printf '%s' "${val:-$default}"
}

csv() { printf '%s' "$1" | tr ',' ' '; }

remove_family() {
    ipt=$1
    command -v "$ipt" >/dev/null 2>&1 || return 0
    while $ipt -C DOCKER-USER -j "$CHAIN" 2>/dev/null; do $ipt -D DOCKER-USER -j "$CHAIN"; done
    if $ipt -L "$CHAIN" -n >/dev/null 2>&1; then
        $ipt -F "$CHAIN"
        $ipt -X "$CHAIN"
    fi
}

# $1 = iptables | ip6tables, $2 = 4 | 6
apply_family() {
    ipt=$1 fam=$2
    command -v "$ipt" >/dev/null 2>&1 || return 0
    if ! $ipt -L DOCKER-USER -n >/dev/null 2>&1; then
        [ "$fam" = 4 ] && { echo "XATO: DOCKER-USER zanjiri yo'q — Docker ishlayaptimi?" >&2; exit 1; }
        return 0   # IPv6 Docker'da o'chirilgan
    fi
    $ipt -N "$CHAIN" 2>/dev/null || $ipt -F "$CHAIN"

    # NetFlow: faqat FTD eksporterlaridan (eksporterlar IPv4 — IPv6 orqali NetFlow butunlay yopiladi).
    if [ "$fam" = 4 ]; then
        for ip in $(csv "$EXPORTERS"); do
            $ipt -A "$CHAIN" -p udp -s "$ip" -m conntrack --ctorigdstport "$NSEL_PORT" --ctdir ORIGINAL -j RETURN
        done
    fi
    $ipt -A "$CHAIN" -p udp -m conntrack --ctorigdstport "$NSEL_PORT" --ctdir ORIGINAL -j DROP

    # Web: ruxsat etilgan tarmoqlar berilgan bo'lsa — faqat ulardan.
    if [ -n "$WEB_NETS" ]; then
        for port in $WEB_PORTS; do
            if [ "$fam" = 4 ]; then
                for net in $(csv "$WEB_NETS"); do
                    $ipt -A "$CHAIN" -p tcp -s "$net" -m conntrack --ctorigdstport "$port" --ctdir ORIGINAL -j RETURN
                done
            fi
            $ipt -A "$CHAIN" -p tcp -m conntrack --ctorigdstport "$port" --ctdir ORIGINAL -j DROP
        done
    fi
    $ipt -A "$CHAIN" -j RETURN
    $ipt -C DOCKER-USER -j "$CHAIN" 2>/dev/null || $ipt -I DOCKER-USER 1 -j "$CHAIN"
}

case "${1:-}" in
    --remove)
        remove_family iptables
        remove_family ip6tables
        echo "AgentMon firewall qoidalari olib tashlandi."
        exit 0 ;;
    --show)
        iptables -L "$CHAIN" -n -v --line-numbers
        exit 0 ;;
    "") ;;
    *) echo "Foydalanish: $0 [--show|--remove]" >&2; exit 2 ;;
esac

[ "$(id -u)" = 0 ] || { echo "XATO: root huquqi kerak (sudo)." >&2; exit 1; }

EXPORTERS=$(get NSEL_EXPORTERS "")
NSEL_PORT=$(get NSEL_PORT 2055)
WEB_NETS=$(get WEB_ALLOWED_NETS "")
WEB_PORTS="$(get WEB_HTTPS_PORT 8443) $(get WEB_PORT 8088)"

if [ -z "$EXPORTERS" ]; then
    echo "XATO: NSEL_EXPORTERS bo'sh (.env). FTD IP'larini yozing — aks holda NetFlow porti hammaga yopiladi." >&2
    exit 1
fi

apply_family iptables 4
apply_family ip6tables 6

echo "AgentMon firewall qo'llandi:"
echo "  NetFlow UDP $NSEL_PORT  <- faqat: $EXPORTERS"
if [ -n "$WEB_NETS" ]; then
    echo "  Web TCP $WEB_PORTS  <- faqat: $WEB_NETS"
else
    echo "  Web TCP $WEB_PORTS  <- cheklanmagan (WEB_ALLOWED_NETS bo'sh)"
fi

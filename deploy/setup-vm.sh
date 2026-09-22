#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  AgentMon — bo'sh Ubuntu VM'ni bitta buyruq bilan tayyorlash                    ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
#
# Internetga faqat korporativ proxy orqali chiqadigan server uchun (Squid VM bilan bir xil sxema):
#   proxy (apt, Docker, git) → Docker → kod → .env (savol-javob) → build → ishga tushirish → firewall → tekshiruv.
# Qayta ishga tushirish xavfsiz: bor sozlamalar saqlanadi, faqat yetishmagani so'raladi.
#
# Bosqichma-bosqich: avval faqat FTD (NetFlow) + web, qolgan manbalar keyin bittadan qo'shiladi.
#
# Foydalanish (VM'da, root huquqi bilan):
#   sudo bash setup-vm.sh              # 1-bosqich: FTD (NetFlow) + web (yoki davom ettirish)
#   sudo bash setup-vm.sh add ad       # keyingi bosqichlar: Active Directory,
#   sudo bash setup-vm.sh add cortex   #   Cortex XDR,
#   sudo bash setup-vm.sh add ksc      #   Kaspersky Security Center (istalgan tartibda)
#   sudo bash setup-vm.sh update       # yangi kod: git pull → build → qayta ishga tushirish
#   sudo bash setup-vm.sh check        # faqat tekshiruv (ulanishlar, servislar)
#   sudo bash setup-vm.sh admin-password   # favqulodda admin parolini yangilash
#
# Kod qayerdan olinadi (birinchisi mos kelgani):
#   1) skript loyiha papkasi ichidan ishga tushirilgan bo'lsa — o'sha papka;
#   2) AGENTMON_ARCHIVE=/yo'l/agentmon.tar.gz — arxivdan;
#   3) GitHub'dan (repo yopiq bo'lsagina faqat o'qish huquqli fine-grained token so'raladi).
#
# Savol berilmasin desangiz, javoblarni muhit o'zgaruvchisi sifatida bering (nomlari .env dagi bilan bir xil):
#   sudo PROXY=http://172.25.1.10:3128 NSEL_EXPORTERS=172.25.0.1 ... bash setup-vm.sh
set -Eeuo pipefail

REPO_URL=${AGENTMON_REPO:-https://github.com/DiorDevv/AgentMon.git}
BRANCH=${AGENTMON_BRANCH:-main}
DIR=${AGENTMON_DIR:-/opt/agentmon}
STATE_FILE=/etc/agentmon/setup.conf           # proxy manzili (update rejimi uchun)
GIT_CRED_FILE=/etc/agentmon/git-credentials   # GitHub token (faqat root o'qiydi)
LOG_FILE=/var/log/agentmon-setup.log
NO_PROXY_BASE="localhost,127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,postgres,redis,api,logstash,engine,web"

# ------------------------------------------------------------------ yordamchilar
if [ -t 1 ]; then C_B=$'\e[1m'; C_G=$'\e[32m'; C_Y=$'\e[33m'; C_R=$'\e[31m'; C_0=$'\e[0m'; else C_B= C_G= C_Y= C_R= C_0=; fi
STEP=0
step() { STEP=$((STEP + 1)); printf '\n%s== %d. %s%s\n' "$C_B" "$STEP" "$*" "$C_0"; }
ok()   { printf '  %s✔%s %s\n' "$C_G" "$C_0" "$*"; }
warn() { printf '  %s!%s %s\n' "$C_Y" "$C_0" "$*"; WARNINGS+=("$*"); }
die()  { printf '\n%sXATO:%s %s\n' "$C_R" "$C_0" "$*" >&2; exit 1; }
WARNINGS=()
trap 'die "kutilmagan xato ${BASH_SOURCE[0]}:${LINENO} qatorida (buyruq: ${BASH_COMMAND}). Log: $LOG_FILE"' ERR

SELF=$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")
# Terminal bo'lmasa (masalan, avtomatik ishga tushirish) — javoblar stdin'dan o'qiladi.
TTY=/dev/tty
{ : <"$TTY"; } 2>/dev/null || TTY=/dev/stdin

# ask NOM "savol" [standart] [secret]
# Javob: muhit o'zgaruvchisi (berilgan bo'lsa) → foydalanuvchi → standart qiymat.
ask() {
    local name=$1 question=$2 default=${3:-} secret=${4:-} answer
    if [ -n "${!name+x}" ] && [ -n "${!name}" ]; then return 0; fi
    if [ -n "$secret" ]; then
        local hint=""; [ -n "$default" ] && hint=" [o'zgarishsiz qoldirish: Enter]"
        printf '  %s%s: ' "$question" "$hint" >&2
        IFS= read -rs answer <"$TTY" || true; printf '\n' >&2
    else
        local hint=""; [ -n "$default" ] && hint=" [$default]"
        printf '  %s%s: ' "$question" "$hint" >&2
        IFS= read -r answer <"$TTY" || true
    fi
    printf -v "$name" '%s' "${answer:-$default}"
}

# .env bilan ishlash — Python orqali: parolda $, /, &, \ bo'lsa ham buzilmaydi.
env_get() {
    python3 - "$DIR/.env" "$1" <<'PY'
import sys, re
path, key = sys.argv[1], sys.argv[2]
val = ""
for line in open(path, encoding="utf-8"):
    m = re.match(r"^\s*" + re.escape(key) + r"=(.*)$", line.rstrip("\n"))
    if m:
        val = m.group(1).strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
print(val)
PY
}

env_set() {  # env_set KEY VALUE  — qiymat har doim bitta tirnoqda yoziladi (docker compose uni aynan o'qiydi)
    python3 - "$DIR/.env" "$1" "$2" <<'PY'
import sys, re, os
path, key, val = sys.argv[1], sys.argv[2], sys.argv[3]
if "'" in val or "\n" in val:
    sys.exit(f"{key}: qiymatda bitta tirnoq (') yoki yangi qator bo'lishi mumkin emas")
line = f"{key}='{val}'" if val else f"{key}="
lines = open(path, encoding="utf-8").read().splitlines()
pat = re.compile(r"^\s*" + re.escape(key) + r"=")
for i, l in enumerate(lines):
    if pat.match(l):
        lines[i] = line
        break
else:
    lines.append(line)
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
os.chmod(tmp, 0o600)
os.replace(tmp, path)
PY
}

# .env dagi qiymat (yo'q yoki namunaviy bo'lsa — bo'sh)
env_current() {
    local v; v=$(env_get "$1")
    case "$v" in
        ""|*corp.local*|BuYerga-*|api-mycompany.*|10.10.0.0/16=Markaz*|10.10.250.0/24*) printf '' ;;
        *) printf '%s' "$v" ;;
    esac
}

need_root() { [ "$(id -u)" = 0 ] || die "root huquqi kerak: sudo bash $SELF ${1:-}"; }

compose() { (cd "$DIR" && docker compose "$@"); }

# ------------------------------------------------------------------ 1. proxy
setup_proxy() {
    step "Internet proxy"
    [ -f "$STATE_FILE" ] && . "$STATE_FILE"
    if [ -z "${PROXY:-}" ]; then
        echo "  Server internetga faqat korporativ proxy orqali chiqadimi? Squid VM'dagi qiymat bilan bir xil:"
        echo "    grep -E '^HTTPS?_PROXY=' ~/squid-watch/.env     (Squid VM'da)"
        ask PROXY "Proxy manzili (masalan http://172.25.1.10:3128; proxy yo'q bo'lsa — bo'sh)" ""
    fi
    PROXY=${PROXY%/}
    if [ -n "$PROXY" ]; then
        [[ "$PROXY" =~ ^https?://[^[:space:]]+:[0-9]+$ ]] || die "proxy formati noto'g'ri: '$PROXY' (kerak: http://IP:PORT)"
    fi
    mkdir -p /etc/agentmon && chmod 700 /etc/agentmon
    printf 'PROXY=%q\n' "$PROXY" >"$STATE_FILE"; chmod 600 "$STATE_FILE"
    NO_PROXY_ALL=$NO_PROXY_BASE

    if [ -z "$PROXY" ]; then ok "proxy ishlatilmaydi (to'g'ridan-to'g'ri internet)"; return; fi

    # apt
    cat >/etc/apt/apt.conf.d/95agentmon-proxy <<EOF
Acquire::http::Proxy "$PROXY";
Acquire::https::Proxy "$PROXY";
EOF
    ok "apt → $PROXY"
    # Shu skript ichidagi curl/git/get.docker.com uchun
    export http_proxy=$PROXY https_proxy=$PROXY HTTP_PROXY=$PROXY HTTPS_PROXY=$PROXY
    export no_proxy=$NO_PROXY_ALL NO_PROXY=$NO_PROXY_ALL
}

check_proxy() {
    [ -z "${PROXY:-}" ] && return 0
    local code
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 -x "$PROXY" https://github.com || true)
    case "$code" in
        2*|3*) ok "proxy ishlayapti (github.com → HTTP $code)" ;;
        407)   die "proxy login/parol talab qilyapti (HTTP 407). Formati: http://login:parol@IP:PORT" ;;
        000)   die "proxy'ga ulanib bo'lmadi ($PROXY). Tarmoq adminidan so'rang: shu VM IP'si ($(hostname -I | awk '{print $1}')) proxy'dan foydalanishga ruxsat etilganmi?" ;;
        *)     die "proxy github.com'ni ochmadi (HTTP $code). Proxy ruxsat ro'yxatiga shu VM IP'sini qo'shish kerak bo'lishi mumkin." ;;
    esac
}

# ------------------------------------------------------------------ 2. paketlar va Docker
install_packages() {
    step "Kerakli paketlar"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq >>"$LOG_FILE" 2>&1 || die "apt-get update ishlamadi (proxy? log: $LOG_FILE)"
    apt-get install -y -qq ca-certificates curl git openssl python3 netcat-openbsd iptables >>"$LOG_FILE" 2>&1 \
        || die "paketlarni o'rnatib bo'lmadi (log: $LOG_FILE)"
    ok "curl, git, openssl, python3, netcat, iptables"
    check_proxy
}

install_docker() {
    step "Docker"
    if ! command -v docker >/dev/null 2>&1; then
        echo "  Docker o'rnatilmoqda (bir necha daqiqa)..."
        if curl -fsSL --max-time 60 https://get.docker.com -o /tmp/get-docker.sh >>"$LOG_FILE" 2>&1 \
                && sh /tmp/get-docker.sh >>"$LOG_FILE" 2>&1; then
            ok "Docker (download.docker.com)"
        else
            warn "get.docker.com ishlamadi — Ubuntu paketlaridan o'rnatilmoqda"
            apt-get install -y -qq docker.io docker-compose-v2 >>"$LOG_FILE" 2>&1 || die "Docker o'rnatilmadi (log: $LOG_FILE)"
            ok "Docker (Ubuntu paketlari)"
        fi
    else
        ok "Docker allaqachon bor: $(docker --version | cut -d, -f1)"
    fi
    docker compose version >/dev/null 2>&1 || die "docker compose (v2) yo'q: apt-get install docker-compose-v2"

    if [ -n "${PROXY:-}" ]; then
        # Docker daemon image'larni (postgres, logstash, node ...) shu proxy orqali yuklaydi.
        mkdir -p /etc/systemd/system/docker.service.d
        local conf=/etc/systemd/system/docker.service.d/http-proxy.conf new
        new=$(printf '[Service]\nEnvironment="HTTP_PROXY=%s"\nEnvironment="HTTPS_PROXY=%s"\nEnvironment="NO_PROXY=%s"\n' \
              "$PROXY" "$PROXY" "$NO_PROXY_ALL")
        if [ "$(cat "$conf" 2>/dev/null)" != "$new" ]; then
            printf '%s\n' "$new" >"$conf"
            systemctl daemon-reload
            systemctl restart docker
            ok "Docker daemon proxy'si sozlandi va qayta ishga tushirildi"
        else
            ok "Docker daemon proxy'si allaqachon sozlangan"
        fi
    fi
    systemctl enable --now docker >/dev/null 2>&1 || true
    docker info >/dev/null 2>&1 || die "Docker ishlamayapti: systemctl status docker"

    # NetFlow UDP buferi (paketlar yo'qolmasligi uchun)
    echo 'net.core.rmem_max=33554432' >/etc/sysctl.d/90-agentmon.conf
    sysctl -q -p /etc/sysctl.d/90-agentmon.conf
    ok "UDP buferi: net.core.rmem_max=33554432"
}

# ------------------------------------------------------------------ 3. kod
get_code() {
    step "AgentMon kodi"
    local here; here=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd || true)
    if [ -z "${AGENTMON_DIR:-}" ] && [ -f "$here/docker-compose.yml" ] && [ -d "$here/backend/agentmon" ]; then
        DIR=$here
        if [ "${MODE:-install}" = update ] && [ -d "$DIR/.git" ]; then
            [ -n "${PROXY:-}" ] && git config --system http.proxy "$PROXY"
            git -C "$DIR" pull -q --ff-only || die "git pull ishlamadi ($DIR)"
            ok "yangilandi: $(git -C "$DIR" log --oneline -1)"
        else
            ok "skript loyiha ichida: $DIR"
        fi
        return
    fi
    if [ -n "${AGENTMON_ARCHIVE:-}" ]; then
        [ -f "$AGENTMON_ARCHIVE" ] || die "arxiv topilmadi: $AGENTMON_ARCHIVE"
        mkdir -p "$DIR"
        tar -xzf "$AGENTMON_ARCHIVE" -C "$DIR" --strip-components=1 --exclude='.env'
        ok "arxivdan ochildi: $DIR (.env saqlandi)"
        return
    fi
    git config --system credential.helper "store --file $GIT_CRED_FILE"
    [ -n "${PROXY:-}" ] && git config --system http.proxy "$PROXY"
    if [ -d "$DIR/.git" ]; then
        git -C "$DIR" fetch -q origin "$BRANCH" && git -C "$DIR" checkout -q "$BRANCH" \
            && git -C "$DIR" pull -q --ff-only origin "$BRANCH" || die "git pull ishlamadi ($DIR)"
        ok "yangilandi: $(git -C "$DIR" log --oneline -1)"
        return
    fi
    # Repo ochiq bo'lsa token kerak emas; yopiq bo'lsagina so'raladi.
    if [ ! -s "$GIT_CRED_FILE" ] && ! GIT_TERMINAL_PROMPT=0 git -c credential.helper= ls-remote -q "$REPO_URL" HEAD >/dev/null 2>&1; then
        echo "  Repo yopiq. GitHub → Settings → Developer settings → Fine-grained tokens:"
        echo "    Repository access: faqat AgentMon;  Permissions → Contents: Read-only"
        local GITHUB_TOKEN=${GITHUB_TOKEN:-}
        ask GITHUB_TOKEN "GitHub token" "" secret
        [ -n "$GITHUB_TOKEN" ] || die "token kiritilmadi (yoki AGENTMON_ARCHIVE bilan arxivdan o'rnating)"
        umask 077
        printf 'https://git:%s@github.com\n' "$GITHUB_TOKEN" >"$GIT_CRED_FILE"
        chmod 600 "$GIT_CRED_FILE"
    fi
    git clone -q --branch "$BRANCH" "$REPO_URL" "$DIR" >>"$LOG_FILE" 2>&1 \
        || { [ -s "$GIT_CRED_FILE" ] && rm -f "$GIT_CRED_FILE"; die "klonlab bo'lmadi: token noto'g'ri yoki repo/branch ($BRANCH) yo'q. Log: $LOG_FILE"; }
    ok "klonlandi: $DIR ($(git -C "$DIR" log --oneline -1))"
}

locate_code() {  # savol bermasdan mavjud o'rnatishni topadi
    local here; here=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd || true)
    if [ -z "${AGENTMON_DIR:-}" ] && [ -f "$here/docker-compose.yml" ]; then DIR=$here; fi
    [ -f "$DIR/.env" ] || die "o'rnatish topilmadi ($DIR/.env yo'q) — avval: sudo bash $SELF"
}

# ------------------------------------------------------------------ 4. sozlamalar (.env)
admin_hash() {  # parol → pbkdf2_sha256 (agentmon.api.auth.hash_password bilan bir xil)
    python3 - "$1" <<'PY'
import base64, hashlib, secrets, sys
salt = secrets.token_bytes(16)
dk = hashlib.pbkdf2_hmac("sha256", sys.argv[1].encode(), salt, 390_000)
print(f"pbkdf2_sha256:390000:{base64.b64encode(salt).decode()}:{base64.b64encode(dk).decode()}")
PY
}

new_admin_password() {
    ADMIN_PASSWORD_PLAIN=$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-20)
    env_set WEB_ADMIN_USER "$(env_get WEB_ADMIN_USER | grep . || echo admin)"
    env_set WEB_ADMIN_PASSWORD_HASH "$(admin_hash "$ADMIN_PASSWORD_PLAIN")"
}

# check_list NOM TUR — vergul bilan ajratilgan IP (ip) yoki subnet (net, "=Sayt" bilan ham) ro'yxatini tekshiradi
check_list() {
    python3 - "$1" "$2" <<'PY'
import ipaddress, sys
kind, value = sys.argv[2], sys.argv[1]
for item in filter(None, (x.strip() for x in value.split(","))):
    try:
        if kind == "ip":
            ipaddress.IPv4Address(item)
        else:
            ipaddress.IPv4Network(item.partition("=")[0].strip(), strict=False)
    except ValueError:
        sys.exit(f"noto'g'ri qiymat: '{item}'")
PY
}

# ask_list NOM "savol" TUR majburiy(1/0) — ro'yxat so'raladi, formati tekshiriladi (3 urinish)
ask_list() {
    local name=$1 question=$2 kind=$3 required=$4 try err
    for try in 1 2 3; do
        ask "$name" "$question" "$(env_current "$name")"
        [ "$kind" = ip ] && printf -v "$name" '%s' "${!name// /}"
        if [ -z "${!name}" ]; then
            [ "$required" = 1 ] || return 0
            echo "  ${C_Y}majburiy qiymat${C_0}"
        elif err=$(check_list "${!name}" "$kind" 2>&1); then
            return 0
        else
            echo "  ${C_Y}$err${C_0}"
        fi
        unset "$name"
    done
    die "$name: to'g'ri qiymat kiritilmadi"
}

# Sozlanmagan (bo'sh yoki .env.example'dagi namunaviy) manbalarni o'chiradi — engine namunaviy
# dc01.corp.local yoki namunaviy KSC paroli bilan ulanishga urinmasin. Haqiqiy sozlamalarga tegmaydi.
disable_unconfigured() {
    if [ -z "$(env_get AD_SERVER)" ] || [ -z "$(env_current AD_PASSWORD)" ]; then
        for k in AD_SERVER AD_BASE_DN AD_DNS_ZONE AD_USER AD_PASSWORD AD_CA_FILE WEB_ALLOWED_GROUP WEB_ADMIN_GROUP; do
            env_set "$k" ""
        done
        env_set AD_VERIFY_TLS false
    fi
    if [ -z "$(env_get CORTEX_FQDN)" ] || [ -z "$(env_current CORTEX_KEY)" ]; then
        env_set CORTEX_FQDN ""; env_set CORTEX_KEY_ID ""; env_set CORTEX_KEY ""
    fi
    if [ -z "$(env_get KSC_URL)" ] || [ -z "$(env_current KSC_PASSWORD)" ]; then
        env_set KSC_URL ""; env_set KSC_PASSWORD ""
    fi
}

# PRODUCTS_ENABLED va NO_PROXY — qaysi manbalar ulanganiga qarab.
sync_derived() {
    local zone; zone=$(env_get AD_DNS_ZONE)
    env_set PRODUCTS_ENABLED "$([ -n "$(env_get AD_SERVER)" ] && echo ad,)cortex,ksc,si"
    env_set NO_PROXY "$NO_PROXY_BASE${zone:+,.$zone}"
}

sources_state() {  # qaysi manbalar ulangan — xulosa uchun
    local s="FTD (NetFlow)"
    [ -n "$(env_get AD_SERVER)" ] && s="$s, AD"
    [ -n "$(env_get CORTEX_FQDN)" ] && s="$s, Cortex"
    [ -n "$(env_get KSC_URL)" ] && s="$s, KSC"
    printf '%s' "$s"
}

# 1-bosqich: infratuzilma + FTD (NetFlow) + web. AD, Cortex, KSC keyin: setup-vm.sh add <manba>
configure() {
    step "Sozlamalar (.env) — 1-bosqich: FTD (NetFlow)"
    cd "$DIR"
    if [ ! -f .env ]; then cp .env.example .env; ok ".env yaratildi"; fi
    chmod 600 .env

    # --- sirlar: bo'sh yoki namunaviy bo'lsa yangisi yaratiladi
    local pg ws
    pg=$(env_get POSTGRES_PASSWORD); ws=$(env_get WEB_SECRET)
    if [ -z "$pg" ] || [ "$pg" = 3f9a1c7e5b2d8f604a1e9c3b7d5f2a8e6c4b1d9f ]; then
        if docker volume ls -q 2>/dev/null | grep -q '_pg-data$' && [ -n "$pg" ]; then
            warn "POSTGRES_PASSWORD namunaviy, lekin baza allaqachon yaratilgan — o'zgartirilmadi (docs: parolni almashtirish)"
        else
            env_set POSTGRES_PASSWORD "$(openssl rand -hex 24)"; ok "POSTGRES_PASSWORD yaratildi"
        fi
    fi
    if [ ${#ws} -lt 32 ] || [ "$ws" = b7e4c1a9f2d85e3b6a0c7f1d9e4b2a8c5f3e7d1b9a6c4e2f8d0b3a5c7e9f1d2b ]; then
        env_set WEB_SECRET "$(openssl rand -hex 32)"; ok "WEB_SECRET yaratildi"
    fi

    # --- proxy
    env_set HTTP_PROXY "${PROXY:-}"
    env_set HTTPS_PROXY "${PROXY:-}"

    # --- resurslar: VM hajmiga qarab
    local mem_gb cpus
    mem_gb=$(awk '/MemTotal/ {printf "%d", $2/1024/1024 + 0.5}' /proc/meminfo)
    cpus=$(nproc)
    if [ "$mem_gb" -ge 15 ]; then env_set LS_JAVA_OPTS "-Xms2g -Xmx2g"; env_set REDIS_MAXMEMORY 4gb
    elif [ "$mem_gb" -ge 7 ]; then env_set LS_JAVA_OPTS "-Xms1g -Xmx1g"; env_set REDIS_MAXMEMORY 2gb
    else warn "RAM ${mem_gb} GB — kam (tavsiya: 16 GB). Minimal sozlamalar qo'yildi"
         env_set LS_JAVA_OPTS "-Xms512m -Xmx512m"; env_set REDIS_MAXMEMORY 512mb; fi
    env_set NSEL_WORKERS "$(( cpus > 8 ? 8 : cpus ))"
    ok "resurslar: ${mem_gb} GB RAM, ${cpus} CPU → Logstash $(env_get LS_JAVA_OPTS | awk '{print $2}'), Redis $(env_get REDIS_MAXMEMORY), NSEL_WORKERS=$(env_get NSEL_WORKERS)"

    disable_unconfigured

    # Bir marta so'ralgan bo'lsa — qayta so'ralmaydi (RECONFIGURE=1 bilan qaytadan).
    if [ "$(env_get AGENTMON_CONFIGURED)" = 1 ] && [ "${RECONFIGURE:-0}" != 1 ]; then
        sync_derived
        ok "sozlamalar avval kiritilgan (qayta so'rash: sudo RECONFIGURE=1 bash $SELF)"
        return
    fi

    echo
    echo "  ${C_B}Savollar${C_0} (qavs ichidagi qiymat — Enter bosilsa shu qoladi)"

    echo; echo "  ${C_B}NetFlow (FTD)${C_0}"
    ask_list NSEL_EXPORTERS "FTD'lar IP'lari (flow-export interfeysi), vergul bilan" ip 1
    env_set NSEL_EXPORTERS "$NSEL_EXPORTERS"
    local ad_on=0; [ -n "$(env_get AD_SERVER)" ] && ad_on=1
    if [ "$ad_on" = 1 ]; then
        ask_list USER_SUBNETS "Foydalanuvchi subnetlari (bo'sh = AD Sites'dan avtomatik), masalan 10.10.0.0/16=Markaz" net 0
    else
        echo "  AD hali ulanmagan — foydalanuvchi subnetlari qo'lda kiritiladi (aks holda serverlar ham hisobga olinadi)"
        ask_list USER_SUBNETS "Foydalanuvchi subnetlari, masalan 10.10.0.0/16=Markaz,10.20.0.0/16=Filial" net 1
    fi
    env_set USER_SUBNETS "$USER_SUBNETS"
    ask_list EXCLUDE_SUBNETS "Hisobga olinmaydigan subnetlar (printer, telefon, server VLAN; bo'sh bo'lishi mumkin)" net 0
    env_set EXCLUDE_SUBNETS "$EXCLUDE_SUBNETS"
    ask_list DC_IPS "Domain Controller IP'lari (ixtiyoriy — \"domen trafigi bor/yo'q\" ko'rsatkichi uchun)" ip 0
    env_set DC_IPS "$DC_IPS"

    echo; echo "  ${C_B}Web interfeys${C_0}"
    local vm_ip; vm_ip=$(hostname -I | awk '{print $1}')
    ask WEB_TLS_CN "Brauzerda ochiladigan nom" "$(env_current WEB_TLS_CN | grep . || hostname -f 2>/dev/null || hostname)"
    env_set WEB_TLS_CN "$WEB_TLS_CN"
    env_set WEB_TLS_SAN "DNS:$WEB_TLS_CN,DNS:localhost,IP:$vm_ip,IP:127.0.0.1"
    ask_list WEB_ALLOWED_NETS "Web'ga faqat shu tarmoqlardan kirish (IT/SOC subnetlari; bo'sh = cheklanmagan)" net 0
    env_set WEB_ALLOWED_NETS "$WEB_ALLOWED_NETS"

    sync_derived
    if [ -z "$(env_get WEB_ADMIN_PASSWORD_HASH)" ]; then new_admin_password; fi
    env_set AGENTMON_CONFIGURED 1
    ok "sozlamalar saqlandi: $DIR/.env (faqat root o'qiydi)"
    [ "$ad_on" = 1 ] || ok "web'ga favqulodda admin bilan kiriladi (AD ulangach — AD login ham)"
}

# ------------------------------------------------------------------ keyingi bosqichlar: manba qo'shish
configure_ad() {
    step "Active Directory"
    local AD_DOMAIN=${AD_DOMAIN:-}
    ask AD_DOMAIN "Domen nomi (masalan corp.uz)" "$(env_current AD_DNS_ZONE)"
    [ -n "$AD_DOMAIN" ] || die "domen nomi kiritilmadi"
    local base_dn; base_dn=$(printf 'DC=%s' "${AD_DOMAIN//./,DC=}")
    env_set AD_BASE_DN "$base_dn"; env_set AD_DNS_ZONE "$AD_DOMAIN"
    ask AD_SERVER "DC manzili" "$(env_current AD_SERVER | grep . || echo "ldaps://dc01.$AD_DOMAIN")"
    env_set AD_SERVER "$AD_SERVER"
    ask AD_USER "Servis hisob (UPN)" "$(env_current AD_USER | grep . || echo "svc_agentmon@$AD_DOMAIN")"
    env_set AD_USER "$AD_USER"
    local AD_PASSWORD=${AD_PASSWORD:-}
    ask AD_PASSWORD "Servis hisob paroli" "$(env_current AD_PASSWORD)" secret
    [ -n "$AD_PASSWORD" ] || die "parol kiritilmadi"
    env_set AD_PASSWORD "$AD_PASSWORD"
    ask WEB_ALLOWED_GROUP "Web'ga kirish guruhi (DN)" "$(env_current WEB_ALLOWED_GROUP | grep . || echo "CN=AgentMon-Users,OU=Groups,$base_dn")"
    env_set WEB_ALLOWED_GROUP "$WEB_ALLOWED_GROUP"
    ask WEB_ADMIN_GROUP "O'zgartirish huquqi guruhi (DN)" "$(env_current WEB_ADMIN_GROUP | grep . || echo "CN=AgentMon-Admins,OU=Groups,$base_dn")"
    env_set WEB_ADMIN_GROUP "$WEB_ADMIN_GROUP"
    local AD_CA=${AD_CA:-}
    ask AD_CA "Ichki CA sertifikati fayli (PEM/CER) — DC sertifikatini tekshirish uchun (bo'sh = tekshirilmaydi)" ""
    if [ -n "$AD_CA" ]; then
        [ -f "$AD_CA" ] || die "CA fayli topilmadi: $AD_CA"
        mkdir -p "$DIR/ca"
        openssl x509 -in "$AD_CA" -out "$DIR/ca/corp-ca.pem" 2>/dev/null \
            || openssl x509 -inform der -in "$AD_CA" -out "$DIR/ca/corp-ca.pem" 2>/dev/null \
            || die "CA fayli PEM ham, DER ham emas: $AD_CA"
        env_set AD_VERIFY_TLS true; env_set AD_CA_FILE /app/ca/corp-ca.pem
        ok "CA o'rnatildi: $(openssl x509 -in "$DIR/ca/corp-ca.pem" -noout -subject)"
    elif [ -z "$(env_get AD_CA_FILE)" ] || [ ! -f "$DIR/ca/corp-ca.pem" ]; then
        env_set AD_VERIFY_TLS false; env_set AD_CA_FILE ""
        warn "AD_VERIFY_TLS=false — DC sertifikati tekshirilmaydi. CA faylini keyin qo'shing: sudo bash $SELF add ad"
    fi
    ok "AD sozlandi ($AD_DOMAIN)"
}

configure_cortex() {
    step "Cortex XDR (Settings → Integrations → API Keys)"
    ask CORTEX_FQDN "API manzili (https:// va / siz)" "$(env_current CORTEX_FQDN)"
    CORTEX_FQDN=${CORTEX_FQDN#https://}; CORTEX_FQDN=${CORTEX_FQDN%%/*}
    [ -n "$CORTEX_FQDN" ] || die "API manzili kiritilmadi"
    env_set CORTEX_FQDN "$CORTEX_FQDN"
    ask CORTEX_KEY_ID "Kalit ID" "$(env_current CORTEX_KEY_ID)"; env_set CORTEX_KEY_ID "$CORTEX_KEY_ID"
    local CORTEX_KEY=${CORTEX_KEY:-}
    ask CORTEX_KEY "Kalit" "$(env_current CORTEX_KEY)" secret
    [ -n "$CORTEX_KEY" ] || die "kalit kiritilmadi"
    env_set CORTEX_KEY "$CORTEX_KEY"
    ask CORTEX_KEY_TYPE "Kalit turi (standard/advanced)" "$(env_get CORTEX_KEY_TYPE | grep . || echo standard)"
    env_set CORTEX_KEY_TYPE "$CORTEX_KEY_TYPE"
    ok "Cortex sozlandi ($CORTEX_FQDN)"
}

configure_ksc() {
    step "Kaspersky Security Center"
    ask KSC_URL "KSC OpenAPI manzili" "$(env_get KSC_URL | grep . || echo https://172.25.25.111:13299)"
    [ -n "$KSC_URL" ] || die "KSC manzili kiritilmadi"
    env_set KSC_URL "$KSC_URL"
    ask KSC_USER "Foydalanuvchi" "$(env_current KSC_USER | grep . || echo agentmon_ro)"; env_set KSC_USER "$KSC_USER"
    local KSC_PASSWORD=${KSC_PASSWORD:-}
    ask KSC_PASSWORD "Parol" "$(env_current KSC_PASSWORD)" secret
    [ -n "$KSC_PASSWORD" ] || die "parol kiritilmadi"
    env_set KSC_PASSWORD "$KSC_PASSWORD"
    ask KSC_DOMAIN "Domen hisobi bo'lsa — NetBIOS domen nomi (KSC ichki foydalanuvchisi bo'lsa bo'sh)" "$(env_get KSC_DOMAIN)"
    env_set KSC_DOMAIN "$KSC_DOMAIN"
    env_set KSC_INTERNAL_USER "$([ -z "$KSC_DOMAIN" ] && echo true || echo false)"
    ok "KSC sozlandi ($KSC_URL)"
}

add_source() {  # add_source ad|cortex|ksc
    local src=${1:-}
    case "$src" in
        ad|cortex|ksc) ;;
        *) die "qaysi manba? sudo bash $SELF add ad | add cortex | add ksc" ;;
    esac
    locate_code
    [ -f "$STATE_FILE" ] && . "$STATE_FILE"
    [ "$(env_get AGENTMON_CONFIGURED)" = 1 ] || die "avval 1-bosqich (FTD): sudo bash $SELF"
    cd "$DIR"
    "configure_$src"
    sync_derived
    step "Engine va API qayta ishga tushirilmoqda"
    compose up -d --force-recreate engine api >>"$LOG_FILE" 2>&1 || die "qayta ishga tushmadi (log: $LOG_FILE)"
    ok "engine, api (NetFlow isinish davri ~10 daqiqa; inventar ~1 daqiqada sinxronlanadi)"
}

# ------------------------------------------------------------------ 5. build va ishga tushirish
build_and_start() {
    step "Build va ishga tushirish (birinchi marta 5–15 daqiqa)"
    compose build >>"$LOG_FILE" 2>&1 || {
        tail -n 25 "$LOG_FILE" | sed 's/^/    /'
        if grep -qiE "certificate verify failed|x509|SSL: CERTIFICATE|self.signed certificate" "$LOG_FILE"; then
            die "build: sertifikat xatosi — proxy HTTPS'ni ochib tekshiryapti (TLS inspection). Tarmoq adminidan shu VM uchun istisno yoki proxy CA sertifikatini so'rang. Log: $LOG_FILE"
        elif grep -qiE "proxyconnect|407|Proxy Authentication|ECONNREFUSED|Could not resolve|Temporary failure in name resolution|timed out" "$LOG_FILE"; then
            die "build: internetga chiqib bo'lmadi (proxy?). Tekshiring: curl -x \$HTTPS_PROXY -I https://pypi.org  Log: $LOG_FILE"
        fi
        die "build muvaffaqiyatsiz — yuqoridagi xato matniga qarang. Log: $LOG_FILE"
    }
    ok "image'lar qurildi"
    compose up -d --remove-orphans >>"$LOG_FILE" 2>&1 || die "ishga tushmadi (log: $LOG_FILE)"
    echo "  servislar sog'lomligini kutish..."
    local i status bad
    for i in $(seq 1 60); do
        status=$(compose ps --format '{{.Service}} {{.Health}}' 2>/dev/null)
        bad=$(awk '$2 != "healthy"' <<<"$status" | wc -l)
        [ "$bad" = 0 ] && [ -n "$status" ] && break
        if grep -q unhealthy <<<"$status"; then break; fi
        sleep 5
    done
    while read -r svc health; do
        if [ "$health" = healthy ]; then ok "$svc"; else warn "$svc: ${health:-ishlamayapti} (docker compose logs $svc)"; fi
    done <<<"$status"
}

setup_firewall() {
    step "Firewall (NetFlow va web portlari)"
    if [ -z "$(env_get NSEL_EXPORTERS)" ]; then
        warn "NSEL_EXPORTERS bo'sh — firewall qo'yilmadi, NetFlow istalgan manzildan qabul qilinadi"
        return
    fi
    ENV_FILE="$DIR/.env" sh "$DIR/deploy/docker-firewall.sh" | sed 's/^/  /'
    sed "s|/opt/agentmon|$DIR|g" "$DIR/deploy/agentmon-firewall.service" >/etc/systemd/system/agentmon-firewall.service
    systemctl daemon-reload
    systemctl enable -q agentmon-firewall
    ok "qayta yuklanishdan keyin ham qo'llanadi (agentmon-firewall.service)"
}

# ------------------------------------------------------------------ 6. tekshiruv
tcp_check() {  # tcp_check nom host port
    if nc -z -w 5 "$2" "$3" 2>/dev/null; then ok "$1: $2:$3 ochiq"; else warn "$1: $2:$3 ga ulanib bo'lmadi (tarmoq ruxsati?)"; fi
}

check_netflow() {
    local col
    col=$(compose exec -T postgres psql -U agentmon -d agentmon -tAc \
        "SELECT value FROM system_status WHERE key = 'collector'" 2>/dev/null || true)
    if [ -z "$col" ]; then
        echo "  (NetFlow statistikasi hali yo'q — engine ishga tushgandan ~1 daqiqa keyin: sudo bash $SELF check)"
        return
    fi
    while IFS='|' read -r level msg; do
        if [ "$level" = OK ]; then ok "$msg"; else warn "$msg"; fi
    done < <(python3 - "$col" "$(env_get NSEL_EXPORTERS)" <<'PY'
import json, sys
c = json.loads(sys.argv[1])
allowed = [x.strip() for x in sys.argv[2].split(",") if x.strip()]
if not c.get("received"):
    print("WARN|NetFlow hali kelmayapti — FTD'da flow-export sozlang (deploy/ftd-netflow.md)")
else:
    print(f"OK|NetFlow: {c['received']} ta hodisa qabul qilindi, {c.get('eps', 0)}/s, kuzatilayotgan IP: {c.get('tracked_ips', 0)}")
    if c.get("stale"):
        print("WARN|NetFlow to'xtagan (oxirgi: %s)" % c.get("last_rx"))
    if not c.get("update_events_seen"):
        print("WARN|flow-update hodisalari yo'q — FTD'da 'flow-export active refresh-interval 5' sozlanmagan (deploy/ftd-netflow.md)")
exps = c.get("exporters") or {}
names = {"ok": "kelyapti", "quiet": "jim (faol hostlar yo'q)", "stale": "TO'XTAGAN", "missing": "hech kelmagan"}
for ip in allowed or sorted(exps):
    st = (exps.get(ip) or {}).get("state", "missing" if c.get("received") else None)
    if st is None:
        continue
    print(f"{'OK' if st in ('ok', 'quiet') else 'WARN'}|FTD {ip}: {names.get(st, st)}")
if c.get("rejected"):
    print(f"WARN|ruxsat etilmagan manbadan {c['rejected']} ta hodisa rad etildi (NSEL_EXPORTERS ni tekshiring)")
PY
)
}

check_all() {
    step "Tekshiruv"
    cd "$DIR"
    check_netflow
    local ad ksc cortex proxy port h p
    ad=$(env_get AD_SERVER); ksc=$(env_get KSC_URL); cortex=$(env_get CORTEX_FQDN); proxy=$(env_get HTTPS_PROXY)
    if [ -n "$ad" ]; then
        h=${ad#*://}; h=${h%%/*}; p=${h##*:}; [ "$p" = "$h" ] && p=$([[ "$ad" == ldaps* ]] && echo 636 || echo 389); h=${h%%:*}
        tcp_check "AD (LDAP)" "$h" "$p"
    fi
    if [ -n "$ksc" ]; then
        h=${ksc#*://}; h=${h%%/*}; p=${h##*:}; [ "$p" = "$h" ] && p=13299; h=${h%%:*}
        tcp_check "KSC OpenAPI" "$h" "$p"
    fi
    if [ -n "$cortex" ]; then
        local code
        code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 ${proxy:+-x "$proxy"} "https://$cortex/" || true)
        if [ "$code" != 000 ]; then ok "Cortex API: https://$cortex javob berdi (HTTP $code${proxy:+, proxy orqali})"
        else warn "Cortex API: https://$cortex ga ulanib bo'lmadi${proxy:+ (proxy orqali)}"; fi
    fi
    port=$(env_get WEB_HTTPS_PORT); port=${port:-8443}
    if curl -sk --max-time 10 "https://127.0.0.1:$port/api/health" | grep -q '"ok"'; then ok "web: https://127.0.0.1:$port/api/health"
    else warn "web javob bermadi: docker compose -f $DIR/docker-compose.yml logs web api"; fi
    local sources
    sources=$(compose exec -T postgres psql -U agentmon -d agentmon -tAc \
        "SELECT source || ': ' || coalesce(item_count::text, '-') || ' ta' || coalesce(' — XATO: ' || left(last_error, 120), '')
         FROM source_status ORDER BY source" 2>/dev/null || true)
    if [ -n "$sources" ]; then
        while IFS= read -r line; do
            if [[ "$line" == *XATO* ]]; then warn "inventar $line"; else ok "inventar $line"; fi
        done <<<"$sources"
    else
        echo "  (inventar hali sinxronlanmagan — engine ishga tushgandan ~1 daqiqa keyin: sudo bash $SELF check)"
    fi
}

summary() {
    local port ip; port=$(env_get WEB_HTTPS_PORT); port=${port:-8443}; ip=$(hostname -I | awk '{print $1}')
    printf '\n%s══════════════════════════════════════════════════════════════════%s\n' "$C_B" "$C_0"
    printf '%sAgentMon tayyor%s\n' "$C_G" "$C_0"
    printf '  Web:          https://%s:%s   (yoki https://%s:%s)\n' "$ip" "$port" "$(env_get WEB_TLS_CN)" "$port"
    if [ -n "${ADMIN_PASSWORD_PLAIN:-}" ]; then
        printf '  Favqulodda admin: %s / %s%s%s   ← HOZIR saqlab qo'"'"'ying, qayta ko'"'"'rsatilmaydi\n' \
            "$(env_get WEB_ADMIN_USER)" "$C_B" "$ADMIN_PASSWORD_PLAIN" "$C_0"
    fi
    printf '  Papka:        %s   (sozlamalar: .env, log: %s)\n' "$DIR" "$LOG_FILE"
    printf '  Yangilash:    sudo bash %s/deploy/setup-vm.sh update\n' "$DIR"
    printf '  Tekshirish:   sudo bash %s/deploy/setup-vm.sh check\n' "$DIR"
    printf '  Ulangan:      %s\n' "$(sources_state)"
    if [ ${#WARNINGS[@]} -gt 0 ]; then
        printf '\n%sE'"'"'tibor talab:%s\n' "$C_Y" "$C_0"
        printf '  - %s\n' "${WARNINGS[@]}"
    fi
    printf '\nKeyingi qadam: FTD'"'"'da NetFlow eksportini yoqish — %s/deploy/ftd-netflow.md\n' "$DIR"
    printf '  (manzil: %s, UDP %s; FTD interfeys IP'"'"'si NSEL_EXPORTERS da bo'"'"'lishi shart)\n' "$ip" "$(env_get NSEL_PORT | grep . || echo 2055)"
    local next=()
    [ -n "$(env_get AD_SERVER)" ] || next+=(ad)
    [ -n "$(env_get CORTEX_FQDN)" ] || next+=(cortex)
    [ -n "$(env_get KSC_URL)" ] || next+=(ksc)
    if [ ${#next[@]} -gt 0 ]; then
        printf '\nKeyingi bosqichlar (NetFlow kelayotgani tasdiqlangandan keyin, bittadan):\n'
        for src in "${next[@]}"; do printf '  sudo bash %s/deploy/setup-vm.sh add %s\n' "$DIR" "$src"; done
    fi
}

# ------------------------------------------------------------------ asosiy
main() {
    local mode=${1:-install}
    MODE=$mode
    need_root "$mode"
    mkdir -p "$(dirname "$LOG_FILE")"; : >>"$LOG_FILE"; chmod 600 "$LOG_FILE"
    printf '\n[%s] setup-vm.sh %s\n' "$(date -Is)" "$mode" >>"$LOG_FILE"
    case "$mode" in
        install)
            . /etc/os-release 2>/dev/null || true
            [ "${ID:-}" = ubuntu ] || warn "Ubuntu emas (${PRETTY_NAME:-aniqlanmadi}) — sinalmagan tizim"
            setup_proxy; install_packages; install_docker; get_code; configure; build_and_start; setup_firewall
            check_all; summary ;;
        update)
            setup_proxy; get_code; configure; build_and_start; setup_firewall; check_all; summary ;;
        check)
            locate_code; check_all ;;
        add)
            add_source "${2:-}"; check_all; summary ;;
        admin-password)
            locate_code; new_admin_password; compose up -d --force-recreate api >>"$LOG_FILE" 2>&1
            printf 'Yangi favqulodda admin: %s / %s\n' "$(env_get WEB_ADMIN_USER)" "$ADMIN_PASSWORD_PLAIN" ;;
        -h|--help|help) sed -n '2,27p' "$SELF" ;;
        *) die "noma'lum rejim: $mode (install | update | check | add <ad|cortex|ksc> | admin-password)" ;;
    esac
}

[ "${AGENTMON_SETUP_SOURCE_ONLY:-0}" = 1 ] || main "$@"

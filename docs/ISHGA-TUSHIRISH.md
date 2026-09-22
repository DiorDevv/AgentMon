# AgentMon: real muhitda ishga tushirish qo'llanmasi

Bu hujjat AgentMon'ni noldan real ishlaydigan holatga keltirish uchun barcha qadamlarni tartib bilan beradi.
Qadamlarni **aynan shu tartibda** bajaring: har bir qadam oldingisiga tayanadi.

Har bir qadamda:
- 🖥️ **Qayerda** bajariladi (qaysi kompyuter yoki tizimda);
- ⌨️ **Nima** qilinadi (buyruq yoki amal);
- ✅ **Kutilgan natija** (qadam to'g'ri bajarilganini qanday bilasiz);
- ❌ **Agar ishlamasa** nima qilish kerak.

**Umumiy vaqt:** tayyorgarlik 1–2 kun (asosan ruxsatlar olish), o'rnatish 1–2 soat, pilot 1–2 hafta.

---

> **AD hozircha ulanmayaptimi?** Tizim AD'siz ham ishlaydi — [AD'siz rejim](#adsiz-rejim-vaqtinchalik) bo'limiga qarang.
> Bunda 3.1–3.2 qadamlar va 2-qadamdagi DC (636) ruxsati kerak emas.

## Mundarija

| Bosqich | Nima qilinadi | Kim bajaradi |
|---|---|---|
| [0. Tayyorgarlik ro'yxati](#0-tayyorgarlik-royxati) | Kerakli narsalarni yig'ish | Siz |
| [1. Server tayyorlash](#1-server-tayyorlash) | Ubuntu va Docker | Linux admin |
| [2. Tarmoq ruxsatlari](#2-tarmoq-ruxsatlari) | Firewall qoidalari | Tarmoq admin |
| [3. Servis hisoblari](#3-servis-hisoblarini-yaratish) | AD, Cortex, KSC | AD / xavfsizlik adminlari |
| [4. Loyihani serverga ko'chirish](#4-loyihani-serverga-kochirish) | Fayllarni nusxalash | Linux admin |
| [5. Sozlamalar (.env)](#5-sozlamalar-env-fayli) | Qiymatlarni yozish | Siz |
| [6. Ishga tushirish](#6-ishga-tushirish) | `docker compose up` | Linux admin |
| [7. Inventar tekshiruvi](#7-inventar-manbalarini-tekshirish) | AD, Cortex, KSC ulandimi | Siz |
| [8. FTD sozlash](#8-ftd-netflow-eksportini-yoqish) | NetFlow eksport | Tarmoq admin |
| [9. Birinchi natijalar](#9-birinchi-natijalarni-tekshirish) | Zanjir ishlayaptimi | Siz |
| [10. Pilot](#10-pilot-natijalar-togriligini-tekshirish) | Test ssenariylari | Siz + IT |
| [11. Kalibrlash](#11-chegaralarni-kalibrlash) | Chegaralarni moslash | Siz |
| [12. Xavfsizlik va barqarorlik](#12-ishlab-chiqarishga-tayyorlash) | HTTPS, backup | Linux admin |
| [13. Kundalik ishlatish](#13-kundalik-ishlatish) | Buyruqlar, yangilash | Hamma |
| [14. Muammolarni hal qilish](#14-muammolarni-hal-qilish) | Tez-tez uchraydigan xatolar | Hamma |

---

## 0. Tayyorgarlik ro'yxati

Boshlashdan oldin quyidagilarni yig'ing. Qavs ichida qaysi qadamda kerak bo'lishi ko'rsatilgan.

**Server:**
- [ ] Ubuntu 22.04 yoki 24.04 server, virtual mashina bo'lishi mumkin (1-qadam)
  - CPU: 4 vCPU
  - RAM: 12–16 GB (Logstash ~2 GB, PostgreSQL, engine)
  - Disk: 100 GB
- [ ] Serverning statik IP manzili, masalan `172.25.50.10`. Bu hujjatda **`<SERVER_IP>`** deb yoziladi.

**Tarmoq:**
- [ ] Barcha FTD'lar (markaz va filiallar) serverga UDP 2055 port orqali yubora oladi (2-qadam)
- [ ] Server DC, Cortex cloud va KSC'ga ulana oladi (2-qadam)

**Hisoblar va kalitlar (3-qadam):**
- [ ] AD servis hisobi (oddiy foydalanuvchi, admin emas)
- [ ] AD guruhi: web interfeysga kiradiganlar uchun
- [ ] Cortex XDR API key (Viewer roli)
- [ ] KSC'da faqat o'qish huquqli foydalanuvchi

**Ma'lumotlar (5-qadam):**
- [ ] Domen nomi, masalan `corp.local`
- [ ] DC nomi yoki IP'si
- [ ] Xodimlar kompyuterlari subnetlari va ular qaysi saytga tegishli
- [ ] Printer, IP-telefon, kamera va server VLAN'lari (ular hisobdan chiqariladi)

---

## 1. Server tayyorlash

🖥️ **Qayerda:** yangi Ubuntu serverda, SSH orqali.

### 1.1. Tizimni yangilash

```bash
sudo apt update && sudo apt upgrade -y
sudo timedatectl set-timezone Asia/Tashkent
```

> Vaqt muhim: tizim "oxirgi marta qachon ko'rildi" degan ma'lumotga tayanadi. NTP ishlashini tekshiring:
> `timedatectl` → `System clock synchronized: yes`

### 1.2. Docker o'rnatish

```bash
sudo apt install -y docker.io docker-compose-v2
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
```

So'ng **SSH'dan chiqib, qayta kiring** (guruh o'zgarishi kuchga kirishi uchun).

✅ **Kutilgan natija:**
```bash
docker --version          # Docker version 24+ yoki yangiroq
docker compose version    # Docker Compose version v2.x
docker ps                 # xatosiz, bo'sh jadval
```

❌ **Agar `permission denied` chiqsa:** SSH'dan chiqib, qayta kiring.

### 1.3. UDP bufferini oshirish

NetFlow sekundiga minglab UDP paket bilan keladi. Standart bufer kichik bo'lsa, paketlar yo'qoladi.

```bash
echo 'net.core.rmem_max=33554432' | sudo tee /etc/sysctl.d/90-agentmon.conf
sudo sysctl --system
```

✅ `sysctl net.core.rmem_max` → `net.core.rmem_max = 33554432`

### 1.4. Server firewall'i (ufw)

```bash
sudo ufw allow OpenSSH
sudo ufw allow 2055/udp comment 'AgentMon NetFlow'
sudo ufw allow 8088/tcp comment 'AgentMon web'
sudo ufw enable
```

> Keyinchalik (12-qadam) HTTPS sozlanganda 8088 o'rniga 443 ochiladi.

---

## 2. Tarmoq ruxsatlari

🖥️ **Qayerda:** korporativ firewall yoki FTD ACL'lari (tarmoq admini bajaradi).

| # | Qayerdan | Qayerga | Port | Nima uchun |
|---|---|---|---|---|
| 1 | **Barcha FTD'lar** (filiallar ham) | `<SERVER_IP>` | **UDP 2055** | NetFlow (NSEL) |
| 2 | `<SERVER_IP>` | DC | **TCP 636** (LDAPS) | AD: kompyuterlar, DNS, subnetlar |
| 3 | `<SERVER_IP>` | Cortex cloud (`api-xxx.xdr...paloaltonetworks.com`) | **TCP 443** | Cortex API |
| 4 | `<SERVER_IP>` | `172.25.25.111` | **TCP 13299** | KSC OpenAPI |
| 5 | Xodimlar (IT/SOC) | `<SERVER_IP>` | **TCP 8088** (keyinroq 443) | Web interfeys |
| 6 | `<SERVER_IP>` | DNS server | UDP/TCP 53 | Nomlarni aniqlash |

✅ **Tekshirish (serverda):**
```bash
nc -zv dc01.corp.local 636          # "succeeded" chiqishi kerak
nc -zv 172.25.25.111 13299          # "succeeded"
curl -sI https://api-mycompany.xdr.eu.paloaltonetworks.com | head -1   # HTTP javob kelishi kerak
```

❌ **Agar `timed out` yoki `refused` chiqsa:** firewall qoidasi hali qo'yilmagan yoki port yopiq.
LDAPS (636) DC'da yoqilmagan bo'lishi mumkin: DC'da sertifikat o'rnatilgan bo'lishi kerak.
Vaqtincha `ldap://` (389) ishlatish mumkin, lekin tavsiya etilmaydi.

---

## 3. Servis hisoblarini yaratish

### 3.1. AD servis hisobi

🖥️ **Qayerda:** DC yoki RSAT o'rnatilgan kompyuterda, PowerShell (admin sifatida).

```powershell
New-ADUser -Name "svc_agentmon" `
  -SamAccountName "svc_agentmon" `
  -UserPrincipalName "svc_agentmon@corp.local" `
  -Description "AgentMon - faqat o'qish" `
  -AccountPassword (Read-Host -AsSecureString "Parol") `
  -Enabled $true -PasswordNeverExpires $true -CannotChangePassword $true
```

> Oddiy domen foydalanuvchisi **yetarli**: AD'ni o'qish uchun admin huquqi kerak emas.
> Hisobni hech qanday admin guruhga qo'shmang.

### 3.2. Web interfeysga kirish guruhi

```powershell
New-ADGroup -Name "AgentMon-Users" -GroupScope Global -GroupCategory Security `
  -Path "OU=Groups,DC=corp,DC=local" -Description "AgentMon web interfeysiga kirish"

Add-ADGroupMember -Identity "AgentMon-Users" -Members "i.familiyev","a.karimov"

# Guruhning to'liq nomi (DN) — 5-qadamda kerak bo'ladi:
(Get-ADGroup "AgentMon-Users").DistinguishedName
```

✅ Natija, masalan: `CN=AgentMon-Users,OU=Groups,DC=corp,DC=local`. **Nusxa olib qo'ying.**

Shu yerda yana ikkita qiymatni yozib oling:
```powershell
(Get-ADDomain).DistinguishedName     # masalan: DC=corp,DC=local   → AD_BASE_DN
(Get-ADDomain).DNSRoot               # masalan: corp.local         → AD_DNS_ZONE
```

### 3.3. Cortex XDR API key

🖥️ **Qayerda:** Cortex XDR konsoli (brauzerda).

1. **Settings → Configurations → Integrations → API Keys** bo'limiga kiring.
2. **+ New Key** tugmasini bosing.
3. **Security Level:** `Standard`. **Role:** `Viewer` (faqat o'qish).
4. **Comment:** `AgentMon`. Keyin **Generate** tugmasini bosing.
5. Ko'rsatilgan kalitni **darhol nusxalab oling**, u boshqa ko'rsatilmaydi. Bu `CORTEX_KEY`.
6. Jadvaldagi yangi kalitning **ID** ustunidagi raqamni oling. Bu `CORTEX_KEY_ID`.
7. **Copy API URL** tugmasini bosing, masalan `https://api-mycompany.xdr.eu.paloaltonetworks.com/`.
   `https://` va oxiridagi `/` siz qolgan qism `CORTEX_FQDN` bo'ladi.

### 3.4. Kaspersky Security Center foydalanuvchisi

🖥️ **Qayerda:** KSC konsoli (MMC yoki Web Console).

1. **Foydalanuvchilar va rollar → Foydalanuvchilar → Qo'shish** ni tanlang.
2. Nom: `agentmon_ro`, kuchli parol bering.
3. Rolini belgilang: **Auditor** (yoki boshqa faqat o'qish huquqli rol). Administrator rolini bermang.
4. OpenAPI portini tekshiring: **KSC server xususiyatlari → Portlar → Open API port: 13299** yoqilgan bo'lishi kerak.

> Agar KSC'ga alohida foydalanuvchi o'rniga AD hisobi bilan kirmoqchi bo'lsangiz:
> `.env` da `KSC_INTERNAL_USER=false` va `KSC_DOMAIN=CORP` yoziladi.

---

## 4. Loyihani serverga ko'chirish

🖥️ **Qayerda:** loyiha turgan kompyuterda (hozir `/home/dior/Project/Monitoring`).

```bash
cd /home/dior/Project
tar --exclude='Monitoring/web/node_modules' --exclude='Monitoring/web/dist' \
    --exclude='Monitoring/.env' --exclude='**/__pycache__' \
    -czf agentmon.tar.gz Monitoring
scp agentmon.tar.gz <user>@<SERVER_IP>:/tmp/
```

> ⚠️ `.env` ataylab **ko'chirilmaydi**: unda test paroli va test kalitlari bor.
> Serverda yangi `.env` yaratiladi.

🖥️ **Serverda:**
```bash
sudo mkdir -p /opt/agentmon && sudo chown $USER: /opt/agentmon
tar -xzf /tmp/agentmon.tar.gz -C /opt/agentmon --strip-components=1
cd /opt/agentmon && ls
```

✅ Ro'yxatda quyidagilar bo'lishi kerak: `backend  web  logstash  deploy  docs  tools  docker-compose.yml  .env.example  README.md`

---

## 5. Sozlamalar (.env fayli)

🖥️ **Qayerda:** serverda, `/opt/agentmon`.

```bash
cd /opt/agentmon
cp .env.example .env
chmod 600 .env
```

### 5.1. Maxfiy kalitlarni yaratish

```bash
echo "POSTGRES_PASSWORD=$(openssl rand -hex 24)"
echo "WEB_SECRET=$(openssl rand -hex 32)"
```

Chiqqan ikki qatorni `.env` dagi tegishli qatorlar o'rniga qo'ying.

### 5.2. `.env` ni tahrirlash

```bash
nano .env
```

Faylda har bir qator ustida izoh bor. Quyidagilarni **albatta** o'zgartiring:

| Qator | Nima yoziladi | Qayerdan olindi |
|---|---|---|
| `POSTGRES_PASSWORD` | 5.1 natijasi | `openssl` |
| `WEB_SECRET` | 5.1 natijasi | `openssl` |
| `LS_JAVA_OPTS` | `-Xms2g -Xmx2g` (RAM 12 GB va undan ko'p bo'lsa) | — |
| `USER_SUBNETS` | Xodimlar subnetlari. **AD Sites'da bo'lsa, bo'sh qoldiring.** | Tarmoq sxemasi |
| `EXCLUDE_SUBNETS` | Printer, telefon, kamera va server VLAN'lari | Tarmoq sxemasi |
| `AD_SERVER` | `ldaps://dc01.corp.local` | 2-qadam |
| `AD_BASE_DN` | `DC=corp,DC=local` | 3.2 |
| `AD_USER` | `svc_agentmon@corp.local` | 3.1 |
| `AD_PASSWORD` | Servis hisob paroli (**`$` bo'lsa bitta tirnoqda**: `'Pa$$w'`) | 3.1 |
| `AD_DNS_ZONE` | `corp.local` | 3.2 |
| `CORTEX_FQDN` | `api-mycompany.xdr.eu.paloaltonetworks.com` | 3.3 |
| `CORTEX_KEY_ID` | Raqam | 3.3 |
| `CORTEX_KEY` | Uzun kalit | 3.3 |
| `KSC_USER` / `KSC_PASSWORD` | `agentmon_ro` / parol | 3.4 |
| `WEB_ALLOWED_GROUP` | `CN=AgentMon-Users,OU=Groups,DC=corp,DC=local` | 3.2 |

**O'zgartirmaslik kerak bo'lgan qiymatlar:**
- `TARGET_*`: sizning Broker VM, KSC va SI IP'laringiz allaqachon yozilgan.
- `THRESH_*` va boshqa chegaralar: pilotdan keyin moslanadi.

### 5.3. Favqulodda admin parolini o'rnatish

AD ishlamay qolganda ham tizimga kirish uchun kerak:

```bash
docker compose build api
docker compose run --rm api python -m agentmon.api.auth hash
# Parol so'raladi (ekranda ko'rinmaydi). Natija: pbkdf2_sha256:390000:....
```

Chiqqan satrni `.env` ga yozing:
```
WEB_ADMIN_USER=admin
WEB_ADMIN_PASSWORD_HASH=pbkdf2_sha256:390000:....
```

> Kamida 14 belgili, murakkab parol tanlang va uni xavfsiz joyda saqlang.

### 5.4. Sozlamalarni tekshirish

```bash
docker compose config -q && echo "Sozlamalar to'g'ri"
```

✅ `Sozlamalar to'g'ri` chiqishi kerak.
❌ `variable is not set` degan ogohlantirish chiqsa: biror parolda `$` bor, uni bitta tirnoqqa oling.

---

## 6. Ishga tushirish

🖥️ **Qayerda:** serverda, `/opt/agentmon`.

```bash
docker compose up -d --build
```

> ⚠️ `docker-compose.demo.yml` faylini **ishlatmang**, u faqat sinov uchun soxta trafik yaratadi.
> Buning oldini olish uchun qo'shimcha himoya ham bor: real manbalar sozlangan bo'lsa, demo o'zi ishga tushmaydi.

Birinchi build 3–5 daqiqa davom etadi.

✅ **Tekshirish:**
```bash
docker compose ps
```
Oltita servisning barchasi `Up` holatida, `postgres` va `redis` esa `healthy` bo'lishi kerak:
```
api        Up
engine     Up
logstash   Up
postgres   Up (healthy)
redis      Up (healthy)
web        Up
```

```bash
curl -s http://localhost:8088/api/health       # {"ok":true}
docker compose logs --tail 20 engine
```

Engine logida `engine ishga tushdi` yozuvi chiqishi kerak. Keyin, bir necha soniyada, `ad: N ta yozuv`, `cortex: N ta yozuv`, `ksc: N ta yozuv` yozuvlari paydo bo'ladi.

🌐 Brauzerda `http://<SERVER_IP>:8088` ni oching va AD hisobingiz (guruh a'zosi) bilan kiring.

---

## 7. Inventar manbalarini tekshirish

🖥️ **Qayerda:** web interfeys → **Tizim holati** → **Inventar manbalari** jadvali.

| Manba | Kutilgan holat | Yozuvlar soni |
|---|---|---|
| Active Directory | 🟢 Ishlayapti | AD'dagi kompyuter hisoblari soni (~4000+) |
| Cortex XDR API | 🟢 Ishlayapti | Cortex'dagi agentlar soni |
| Kaspersky Security Center | 🟢 Ishlayapti | KSC'dagi Network Agent'li hostlar |

Shu sahifaning pastida yana ikkita narsani tekshiring:
- **Foydalanuvchi subnetlari:** AD Sites'dagi yoki siz yozgan subnetlar ro'yxati to'g'rimi?
- **Signal manzillari → DC'lar:** barcha DC'lar IP'lari bilan ro'yxatda bormi?

❌ **Xato chiqsa**, "Xatolik" ustunida sababi yoziladi. Tuzatish yo'llari [14-bo'limda](#14-muammolarni-hal-qilish).
`.env` ni o'zgartirgandan keyin engine'ni qayta ishga tushiring: `docker compose up -d engine api`

> Inventar har 15 daqiqada yangilanadi. `.env` ni tuzatgach, engine qayta ishga tushirilsa, darhol yangilanadi.

---

## 8. FTD NetFlow eksportini yoqish

🖥️ **Qayerda:** FMC (Firepower Management Center). Tarmoq admini bajaradi.

To'liq ko'rsatma: [`deploy/ftd-netflow.md`](../deploy/ftd-netflow.md). Qisqacha:

1. **Objects → FlexConfig → FlexConfig Object → Add** ni tanlang. Deployment: `Everytime`, Type: `Append`.
2. Quyidagini yozing (`<INTERFEYS>` = serverga yo'l bor interfeys nomi):
   ```
   flow-export destination <INTERFEYS> <SERVER_IP> 2055
   flow-export template timeout-rate 1
   flow-export active refresh-interval 5
   policy-map global_policy
    class class-default
     flow-export event-type all destination <SERVER_IP>
   ```
3. **Devices → FlexConfig** policy'ga qo'shing va FTD'larga biriktiring.
4. **Deploy** qiling.

> **Tavsiya:** avval faqat **markaziy FTD**'ni ulang. 9-qadamdagi tekshiruvdan o'tgach, filiallardagi FTD'larni qo'shing.

✅ **FTD'da tekshirish** (CLI → `system support diagnostic-cli`):
```
show flow-export counters
```
`packets sent` soni oshib borishi kerak, xatolar 0 bo'lishi kerak.

✅ **Serverda tekshirish** (paketlar kelyaptimi):
```bash
sudo tcpdump -ni any udp port 2055 -c 5
```
Bir necha soniyada 5 ta paket ko'rinishi kerak.

---

## 9. Birinchi natijalarni tekshirish

🖥️ **Qayerda:** web interfeys.

**Darhol (1–2 daqiqa ichida)** yuqori panelda yashil **Jonli** indikatori va `N hodisa/s` paydo bo'lishi kerak.

**Tizim holati** sahifasida tekshiring:

| Nima | Kutilgan natija |
|---|---|
| Ma'lumot zanjiri | Barcha bosqichlar 🟢 |
| NetFlow kollektori → Oqim | 0 dan katta (odatda yuzlab yoki minglab hodisa/s) |
| flow-update | 🟢 **kelmoqda** (5–10 daqiqadan keyin) |
| Redis navbati | Kichik son (0–1000). Doimiy o'sib borsa, engine ulgurmayapti. |

**Vaqt jadvali:**

| Vaqt | Nima bo'ladi |
|---|---|
| 0–10 daqiqa | **Isinish davri.** Tizim trafik yig'adi, xulosa chiqarmaydi. Bu normal holat. |
| ~10 daqiqa | "Ishlayapti" holatlari paydo bo'ladi, **Kompyuterlar** sahifasi to'la boshlaydi |
| 30–60 daqiqa | Cortex, KSC va SI bo'yicha "O'rnatilmagan" va "To'xtatilgan" xulosalari chiqadi |
| ~4 soat | AD bo'yicha "DC bilan aloqa yo'q" xulosalari chiqadi |
| 1 kun | Dinamika grafigida birinchi 24 nuqta paydo bo'ladi |

⚠️ **Birinchi soatlarda e'tibor bering:**
- **Noma'lum qurilmalar** ro'yxati uzun bo'lsa, u yerda printerlar, telefonlar va kameralar bo'lishi mumkin. Ularni `EXCLUDE_SUBNETS` ga qo'shing yoki "Ma'lum qurilma" tugmasi bilan belgilang.
- Juda ko'p kompyuterda "IP aniqlanmadi" deyilsa, `AD_DNS_ZONE` ni tekshiring ([14-bo'lim](#14-muammolarni-hal-qilish)).

---

## 10. Pilot: natijalar to'g'riligini tekshirish

Maqsad: tizim xulosalari **haqiqatga mos kelishini** isbotlash.

### 10.1. Test kompyuterlarini tanlash

20–30 ta kompyuterni tanlang, **holati sizga aniq ma'lum** bo'lsin:
- 10 ta: hamma narsa to'g'ri ishlayotgan (nazorat guruhi);
- qolganlari: quyidagi test ssenariylari uchun.

Jadval tuzing: kompyuter nomi, IP, siz kutgan holat va tizim ko'rsatgan holat.

### 10.2. Test ssenariylari

Har birini **test kompyuterida** bajaring va natijani kuting.

| # | Test kompyuterida nima qilinadi | Tizim nima ko'rsatishi kerak | Qancha vaqtda |
|---|---|---|---|
| 1 | Hech narsa (nazorat) | Barcha 4 ta: 🟢 **Ishlayapti** | darhol |
| 2 | Cortex agent servisini to'xtatish (ruxsat bo'lsa) | Cortex: 🔴 **To'xtatilgan** | 30–35 daqiqa |
| 3 | Kaspersky real-time himoyasini o'chirish | KSC: 🟡 **Himoya to'liq emas** ("Real-time himoya to'xtatilgan") | 15–30 daqiqa (KSC sinxronizatsiyasi) |
| 4 | Kaspersky Network Agent servisini to'xtatish | KSC: 🔴 **To'xtatilgan** | 45–50 daqiqa |
| 5 | SearchInform agentini to'xtatish | SI: 🟠 **Signal yo'q** | ~1 soat |
| 6 | Domenga kirmagan noutbukni tarmoqqa ulash | **Noma'lum qurilmalar**da "Domen trafigi yo'q" | 10–15 daqiqa |
| 7 | Kompyuterni o'chirish | 🔘 **Oflayn**, oxirgi holat saqlanadi | 10 daqiqa |
| 8 | Agentni qayta yoqish (2–5-testlardan keyin) | 🟢 **Ishlayapti**, "So'nggi hodisalar"da "tiklandi" | 1–5 daqiqa |

✅ **Pilot muvaffaqiyatli, agar:**
- nazorat guruhida **birorta ham soxta muammo** chiqmasa;
- barcha test ssenariylari to'g'ri aniqlansa.

❌ **Agar mos kelmasa:** o'sha kompyuter sahifasini oching. **Tarmoq** qatorida oxirgi trafik vaqti, **Konsol** qatorida konsol nima deyayotgani ko'rinadi. Skrinshot oling: bu tuzatish uchun eng kerakli ma'lumot.

---

## 11. Chegaralarni kalibrlash

Pilotdan keyin chegaralar sizning muhitingizga moslanadi.

**Agentning haqiqiy "heartbeat" oralig'ini qanday bilish mumkin:** sog'lom kompyuter sahifasini oching va **Tarmoq → oxirgi trafik** qiymatini 5–10 marta kuzating. Eng katta qiymat agentning jimlik oralig'i bo'ladi.

**Qoida:** chegara = eng katta jimlik oralig'i × 2 (zaxira bilan).

| Muammo | Nimani o'zgartirish kerak |
|---|---|
| Sog'lom kompyuterlarda soxta "Signal yo'q" (SI) chiqyapti | `THRESH_SI` ni oshiring (masalan, `7200` = 2 soat) |
| Sog'lom kompyuterlarda soxta "To'xtatilgan" (KSC) chiqyapti | `THRESH_KSC` ni oshiring |
| Muammo juda kech aniqlanyapti | Tegishli `THRESH_*` ni kamaytiring (lekin jimlik oralig'i × 2 dan kam qilmang) |
| Holatlar tez-tez "sakrab" turadi | `DEBOUNCE=3` qiling |

O'zgartirgandan keyin:
```bash
nano .env
docker compose up -d engine api
```

---

## 12. Ishlab chiqarishga tayyorlash

Pilot muvaffaqiyatli bo'lgach, barcha FTD'larni ulashdan **oldin** quyidagilarni bajaring.

### 12.1. HTTPS (majburiy)

Hozir interfeys oddiy HTTP orqali ochiladi. Login paytida AD paroli tarmoqdan **ochiq** ketadi.

Eng oddiy yo'l: serverda nginx'ni ichki CA sertifikati bilan reverse-proxy qilib qo'yish.

1. AgentMon'ni faqat lokal manzilda ochiq qoldiring. `.env` da:
   ```
   WEB_PORT=127.0.0.1:8088
   WEB_COOKIE_SECURE=true
   ```
2. Sertifikatni ichki CA'dan oling (`agentmon.corp.local` uchun). Uni `/etc/ssl/agentmon.crt` va `/etc/ssl/agentmon.key` ga qo'ying.
3. nginx'ni o'rnating va sozlang:
   ```bash
   sudo apt install -y nginx
   sudo tee /etc/nginx/sites-available/agentmon >/dev/null <<'EOF'
   server {
       listen 443 ssl;
       server_name agentmon.corp.local;
       ssl_certificate     /etc/ssl/agentmon.crt;
       ssl_certificate_key /etc/ssl/agentmon.key;
       ssl_protocols TLSv1.2 TLSv1.3;
       location / {
           proxy_pass http://127.0.0.1:8088;
           proxy_set_header Host $host;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto https;
       }
   }
   server { listen 80; server_name agentmon.corp.local; return 301 https://$host$request_uri; }
   EOF
   sudo ln -s /etc/nginx/sites-available/agentmon /etc/nginx/sites-enabled/
   sudo rm -f /etc/nginx/sites-enabled/default
   sudo nginx -t && sudo systemctl reload nginx
   sudo ufw allow 443/tcp && sudo ufw delete allow 8088/tcp
   docker compose up -d
   ```
4. DNS'da `agentmon.corp.local` → `<SERVER_IP>` yozuvini yarating.

✅ `https://agentmon.corp.local` ochiladi, brauzer sertifikat haqida ogohlantirmaydi.

### 12.2. Zaxira nusxa (backup)

Har kuni PostgreSQL bazasining nusxasini oladi va 14 kunlik nusxalarni saqlaydi:

```bash
sudo mkdir -p /var/backups/agentmon
sudo tee /etc/cron.daily/agentmon-backup >/dev/null <<'EOF'
#!/bin/sh
cd /opt/agentmon || exit 1
docker compose exec -T postgres pg_dump -U agentmon agentmon | gzip > /var/backups/agentmon/agentmon-$(date +%F).sql.gz
find /var/backups/agentmon -name '*.sql.gz' -mtime +14 -delete
EOF
sudo chmod +x /etc/cron.daily/agentmon-backup
sudo /etc/cron.daily/agentmon-backup && ls -lh /var/backups/agentmon
```

✅ `agentmon-YYYY-MM-DD.sql.gz` fayli paydo bo'ladi.

**Tiklash** (kerak bo'lganda):
```bash
docker compose stop engine api
gunzip -c /var/backups/agentmon/agentmon-2026-10-01.sql.gz | docker compose exec -T postgres psql -U agentmon agentmon
docker compose start engine api
```

> `.env` faylining nusxasini ham xavfsiz joyda saqlang: unda barcha kalitlar bor.

### 12.3. Kengaytirish

1. Filiallardagi FTD'larda 8-qadamni takrorlang.
2. **Tizim holati** sahifasida oqim oshganini va saytlar paydo bo'lganini tekshiring.
3. Umumiy ko'rinish → **Saytlar bo'yicha holat** jadvalida barcha filiallar ko'rinishi kerak.

---

## 13. Kundalik ishlatish

### Asosiy buyruqlar

Hammasi serverda, `/opt/agentmon` papkasida bajariladi:

| Nima | Buyruq |
|---|---|
| Holatni ko'rish | `docker compose ps` |
| Engine loglari (jonli) | `docker compose logs -f engine` |
| Oxirgi xatolar | `docker compose logs --since 1h engine api \| grep -iE "error\|xato"` |
| Qayta ishga tushirish | `docker compose restart` |
| `.env` o'zgargandan keyin | `docker compose up -d` |
| To'xtatish | `docker compose stop` |
| Ishga tushirish | `docker compose start` |
| Disk hajmi | `docker system df` |

> Server qayta yoqilganda barcha servislar **avtomatik** ko'tariladi (`restart: unless-stopped`).

### Yangilash (yangi versiya chiqqanda)

```bash
cd /opt/agentmon
sudo /etc/cron.daily/agentmon-backup           # avval backup
tar -xzf /tmp/agentmon-yangi.tar.gz -C /opt/agentmon --strip-components=1   # .env ga tegmaydi
docker compose up -d --build
docker compose ps && curl -s http://localhost:8088/api/health
```

### Interfeysdan kundalik foydalanish

| Vazifa | Qayerda |
|---|---|
| Umumiy holat va trend | **Umumiy ko'rinish** |
| Muammoli kompyuterlar ro'yxati | Umumiy ko'rinish → **Muammoli kompyuterlar** |
| Ma'lum filial yoki agent bo'yicha ro'yxat | **Kompyuterlar** → filtrlar |
| Rahbariyat uchun hisobot | **Hisobot (CSV)** yoki Kompyuterlar → **Excel'ga eksport** |
| Bitta kompyuter tarixi | Kompyuter nomini bosing → **Holatlar tarixi** |
| Test stend yoki maxsus kompyuterni hisobdan chiqarish | Kompyuter sahifasi → **Istisno qilish** |
| Printer yoki telefonni noma'lumlar ro'yxatidan olib tashlash | **Noma'lum qurilmalar** → **Ma'lum qurilma** |
| Tizim o'zi ishlayaptimi | **Tizim holati** |

---

## 14. Muammolarni hal qilish

### NetFlow

| Belgi | Sabab | Yechim |
|---|---|---|
| "NetFlow kelmayapti", oqim 0 | FTD yubormayapti yoki port yopiq | FTD: `show flow-export counters`. Server: `sudo tcpdump -ni any udp port 2055 -c 5`. Paket kelmasa, firewall yoki FTD sozlamasi; kelsa, `docker compose logs logstash` |
| "flow-update kelmayapti" | `refresh-interval` sozlanmagan | FlexConfig'ga `flow-export active refresh-interval 5` qo'shing, Deploy qiling |
| Oqim bor, lekin "Kuzatilayotgan IP" 0 | Subnetlar noto'g'ri | Tizim holati → Foydalanuvchi subnetlari. `USER_SUBNETS` ni tekshiring |
| Redis navbati doimiy o'syapti | Engine ulgurmayapti | `docker compose logs engine`. Serverda CPU va RAM yetarli ekanini tekshiring |
| Ba'zi kompyuterlar "tasodifiy" oflayn yoki jim ko'rinadi | UDP paketlar yo'qolyapti (Logstash ulgurmayapti) | `docker compose exec logstash sh -c "grep Udp: /proc/net/snmp"`: `RcvbufErrors` o'sib borsa, `deploy/ftd-netflow.md` → "Yuklama katta bo'lsa" |

### Active Directory

| Xatolik matni (Tizim holati → Inventar manbalari) | Yechim |
|---|---|
| `invalidCredentials` | `AD_USER` va `AD_PASSWORD` ni tekshiring. Parolda `$` bo'lsa, bitta tirnoqqa oling. UPN formati: `svc_agentmon@corp.local` |
| `socket` / `timed out` / `Can't contact` | 636-port yopiq yoki DC'da LDAPS yo'q: `nc -zv dc01 636` |
| `certificate verify failed` | `AD_VERIFY_TLS=false` qiling yoki ichki CA'ni o'rnating |
| Kompyuterlar soni 0 | `AD_BASE_DN` noto'g'ri: `(Get-ADDomain).DistinguishedName` bilan solishtiring |
| Ko'p kompyuterda "IP aniqlanmadi" | `AD_DNS_ZONE` noto'g'ri yoki DNS AD-integrated emas. DNS Manager'dagi zona nomini yozing |

### Cortex XDR

| Xatolik | Yechim |
|---|---|
| `401` / `403` | `CORTEX_KEY`, `CORTEX_KEY_ID` va `CORTEX_KEY_TYPE` (standard/advanced) mosligini tekshiring. Kalit o'chirilmaganmi? |
| `Name or service not known` | `CORTEX_FQDN` noto'g'ri (`https://` va `/` bo'lmasligi kerak) |
| Barcha Cortex hostlarda "Ziddiyat" | Agentlar Broker VM'ni chetlab o'tyapti. `TARGET_CORTEX` porti Broker VM'dagi Agent Proxy portiga mosmi? |

### Kaspersky (KSC)

| Xatolik | Yechim |
|---|---|
| `401` | Login yoki parol xato. Ichki foydalanuvchi uchun `KSC_INTERNAL_USER=true`, domen hisobi uchun `false` + `KSC_DOMAIN` |
| `Connection refused` | 13299 port yopiq yoki OpenAPI o'chirilgan |
| `keskin kamayish ... qabul qilinmadi` | KSC odatdagidan ancha kam host qaytardi. Bu himoya mexanizmi: KSC'ni tekshiring. Kamayish haqiqiy bo'lsa, 3 marta takrorlangach avtomatik qabul qilinadi |
| Holatlar noto'g'ri (masalan, RTP yoqilgan, lekin "to'xtatilgan" deyapti) | Kompyuter sahifasidagi **RTP kodi** qiymatini yozib oling va xabar bering: KSC versiyasiga qarab kodlar farq qilishi mumkin |

### Web interfeys

| Belgi | Yechim |
|---|---|
| Sahifa ochilmaydi | `docker compose ps` → `web` va `api` `Up` holatidami? Port firewall'da ochiqmi? |
| "Login yoki parol noto'g'ri, yoki ruxsat yo'q" | Foydalanuvchi `WEB_ALLOWED_GROUP` guruhida bormi? AD ishlamasa, `admin` bilan kiring |
| "Juda ko'p urinish" | 1 daqiqa kuting (brute-force himoyasi) |
| `502 Bad Gateway` | `docker compose logs api`: API xato bilan to'xtagan bo'lishi mumkin (masalan, `.env` da `WEB_SECRET` qisqa) |

### Yordam so'rashdan oldin to'playdigan ma'lumotlar

```bash
cd /opt/agentmon
docker compose ps > /tmp/agentmon-diag.txt
docker compose logs --tail 200 engine api logstash >> /tmp/agentmon-diag.txt 2>&1
```

Bunga qo'shimcha: **Tizim holati** sahifasining skrinshoti va muammoli kompyuter sahifasining skrinshoti.
⚠️ Fayllarni yuborishdan oldin ichida parol yoki kalit yo'qligini tekshiring.

---

## AD'siz rejim (vaqtinchalik)

AD ulanmaguncha tizim **Cortex XDR, Kaspersky va SearchInform** bilan ishlaydi.

**`.env` da:**
```
PRODUCTS_ENABLED=cortex,ksc,si
AD_SERVER=
AD_BASE_DN=
USER_SUBNETS=10.10.0.0/16=Markaz,10.20.0.0/16=Filial-1      # MAJBURIY
DC_IPS=172.25.10.10,172.25.10.11                            # ixtiyoriy, lekin foydali
WEB_ADMIN_USER=admin
WEB_ADMIN_PASSWORD_HASH=pbkdf2_sha256:...                   # 5.3-qadam
```

**Nima o'zgaradi:**

| | AD bilan | AD'siz |
|---|---|---|
| "AD / Domenda" ustuni | Bor | **Yo'q** (interfeysda ko'rinmaydi, hisobga kirmaydi) |
| Kompyuterlar ro'yxati | AD + Cortex + KSC | Cortex + KSC |
| Hech qaysi agenti yo'q kompyuter | "O'rnatilmagan" | **"Noma'lum qurilmalar"** ro'yxatida |
| IP → kompyuter nomi | AD DNS + agentlar | Faqat agentlar xabar bergan IP |
| Subnetlar | AD Sites'dan avtomatik | **`USER_SUBNETS` dan (qo'lda)** |
| Web'ga kirish | AD guruhi | Faqat lokal `admin` |

**Keyinchalik AD'ni qo'shish:** `.env` da AD qiymatlarini to'ldiring, `PRODUCTS_ENABLED=ad,cortex,ksc,si` qiling
va `docker compose up -d` buyrug'ini bering. Ma'lumotlarni o'chirish kerak emas.

---

## Qisqacha: butun jarayon bir sahifada

```
 0. Tayyorgarlik    → server, ruxsatlar, hisoblar ro'yxati
 1. Server          → Ubuntu + Docker + sysctl + ufw
 2. Tarmoq          → UDP 2055 (FTD→server), 636 (→DC), 443 (→Cortex), 13299 (→KSC)
 3. Hisoblar        → svc_agentmon (AD), AgentMon-Users (guruh), Cortex API key, KSC agentmon_ro
 4. Ko'chirish      → /opt/agentmon (.env siz)
 5. .env            → maxfiy kalitlar + AD/Cortex/KSC qiymatlari + admin hash
 6. Ishga tushirish → docker compose up -d --build        (demo faylisiz!)
 7. Inventar        → Tizim holati: 3 ta manba 🟢
 8. FTD             → FlexConfig + refresh-interval 5 (avval markaziy FTD)
 9. Birinchi natija → Jonli 🟢, 10 daqiqa isinish, 30–60 daqiqada xulosalar
10. Pilot           → 20–30 kompyuter, 8 ta test ssenariy
11. Kalibrlash      → THRESH_* ni moslash
12. Production      → HTTPS + backup + barcha FTD'lar
```

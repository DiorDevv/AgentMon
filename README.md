# AgentMon: xavfsizlik agentlari qamrovi monitoringi

~4000 ta kompyuterda quyidagilarni doimiy aniqlaydi:
- kompyuter **domenda** ekanini va DC bilan ishlayotganini;
- **Cortex XDR**, **Kaspersky** va **SearchInform** agentlari o'rnatilgan va ishlayotganini;
- tarmoqda faol, lekin inventarda yo'q **noma'lum qurilmalarni**.

> 📘 **Real muhitda ishga tushirish:** qadam-baqadam qo'llanma — [docs/ISHGA-TUSHIRISH.md](docs/ISHGA-TUSHIRISH.md).
> Loyiha tarixi va qabul qilingan qarorlar — [docs/LOYIHA-TARIXI.md](docs/LOYIHA-TARIXI.md).

## Qanday ishlaydi

Asosiy g'oya: **o'rnatilmagan agent o'zi haqida xabar bermaydi.** Shuning uchun tizim ikki mustaqil dalilni solishtiradi:

1. **Tarmoq dalili (Cisco FTD NSEL).** Har bir agent o'z serveriga muntazam murojaat qiladi. Kompyuter tarmoqda tirik bo'lsa-yu, serverga trafik yo'q bo'lsa, agent ishlamayapti. Bu dalilni endpoint tarafidan aldab bo'lmaydi.
2. **Konsol dalili (API).** AD LDAP, Cortex XDR API va KSC OpenAPI. Konsol hostni biladimi, himoya yoqilganmi, bazalar yangimi, shularni ko'rsatadi.

| Mahsulot | Tarmoq signali | Konsol |
|---|---|---|
| AD | DC'larga 88/389/445 (DC'lar AD'dan avtomatik olinadi) | kompyuter hisobi, `enabled` |
| Cortex XDR | `172.25.44.50:8888` (Broker VM) | `endpoint_status`, `operational_status` |
| Kaspersky | `172.25.25.111:13000/14000` | Network Agent, KES, real-time himoya, bazalar yoshi |
| SearchInform | `172.25.43.205:8090` | — (faqat tarmoq) |

**IP → kompyuter moslash.** DHCP va DC loglari yo'q, shuning uchun uchta manba birlashtiriladi: AD-integrated DNS yozuvlari (LDAP orqali o'qiladi), Cortex agentlari xabar bergan IP va KSC agentlari xabar bergan IP. Asosiy xavf shundaki, DHCP IP'ni boshqa qurilmaga berib yuboradi, eski DNS yozuvi esa hali eski egasini ko'rsatib turadi. Bunga qarshi uchta qoida ishlaydi:
1. Bitta IP bir nechta nomga ko'rsatsa, eng yangi dalil g'olib bo'ladi.
2. Agent hostning hozirgi IP'sini yangi xabar qilgan bo'lsa, o'sha hostning boshqa IP'lardagi eski yozuvlari bekor qilinadi.
3. Faqat eski DNS dalili bilan moslangan faol IP DC bilan trafik ko'rsatishi shart. Aks holda moslash ishonchsiz deb hisoblanadi va IP "noma'lum qurilma" sifatida ko'rsatiladi.

**Holatlar:**

| Holat | Ma'nosi |
|---|---|
| Ishlayapti | Trafik bor, konsol sog'lom |
| Himoya to'liq emas | Trafik bor, lekin konsol muammo ko'rsatmoqda (RTP o'chiq, bazalar eski, qisman himoya), **yoki** agent serverga ulanmoqchi, lekin FTD uni rad etmoqda (ACL) |
| To'xtatilgan | Konsolda bor, host tirik, lekin serverga trafik yo'q |
| O'rnatilmagan | Konsolda yo'q va trafik yo'q |
| Signal yo'q | Trafik yo'q, konsol ma'lumoti yo'q (SearchInform yoki manba eskirgan) |
| Ziddiyat | Tarmoq va konsol bir-biriga zid |
| Tekshirilmoqda | Host yaqinda yoqilgan, xulosa chiqarishga hali erta |
| Oflayn | Host tarmoqda ko'rinmayapti (oxirgi ma'lum holat saqlanadi) |

**Soxta ogohlantirishlardan himoya:**
- Salbiy xulosa faqat host yetarlicha uzoq tirik bo'lgandagina chiqariladi.
- Muammo holatiga o'tish 2 marta ketma-ket tasdiqlanishi kerak (debounce).
- **Ommaviy uzilish.** Oldin OK bo'lgan hostlarning ≥20% qismi birdan jim bo'lsa, bu Broker VM yoki server muammosi deb qaraladi. Tizim yuzlab "to'xtatilgan" holati o'rniga bitta insident chiqaradi.
- **Kollektor to'xtasa** (NSEL kelmasa), holatlar muzlatiladi va ogohlantirish chiqadi.
- API noto'g'ri yoki yarim javob qaytarsa (yozuvlar soni keskin kamaysa), bu ma'lumot qabul qilinmaydi.
- Engine orqada qolsa (masalan, Redis navbati to'lib ketsa), vaqt hodisalar bo'yicha hisoblanadi, shuning uchun hostlar "jim" bo'lib ketmaydi.
- **Isinish davri.** Engine qayta ishga tushgandan yoki NSEL uzilishidan keyin, barcha hostlar trafik yuborib ulgurmaguncha baholash boshlanmaydi. Shu tufayli "hamma birdan oflayn" to'lqini bo'lmaydi.
- **Bitta nusxa.** Engine PostgreSQL advisory lock bilan himoyalangan: ikkinchi nusxa birinchisi to'xtamaguncha kutib turadi.
- Holat o'zgarishi xotiraga faqat DB'ga muvaffaqiyatli yozilgandan keyin olinadi.
- **Retention.** 30 kundan eski IP signallari, 400 kundan eski tarix va statistika avtomatik tozalanadi.

## Arxitektura

```
FTD (NSEL v9, UDP 2055) ─► Logstash ─► Redis ─► Engine (Python) ─► PostgreSQL ─► API (FastAPI) ─► Web (React/nginx)
                                                   ▲
                             AD LDAP + AD DNS, Cortex API, KSC OpenAPI
```

| Servis | Vazifasi |
|---|---|
| `logstash` | NSEL'ni dekodlaydi va ixcham JSON'ni Redis'ga yozadi. Stateless. |
| `redis` | Bufer: engine qayta ishga tushsa ham hodisalar yo'qolmaydi. |
| `engine` | Signallar (xotirada), inventar sinxronizatsiyasi, IP moslash, qoidalar. **Faqat 1 nusxa.** |
| `api` | Web uchun REST API va AD orqali autentifikatsiya. |
| `web` | Interfeys (nginx), `/api` so'rovlarini `api` servisiga proksi qiladi. |

Unumdorlik: ingest bitta yadroda sekundiga ~250 ming hodisani qayta ishlaydi. 4000 host uchun baholash ~0.5 soniya davom etadi.

## O'rnatish (Ubuntu + Docker)

```bash
cp .env.example .env
# .env ni to'ldiring: parollar, AD, Cortex, KSC, WEB_SECRET (openssl rand -hex 32)
chmod 600 .env
docker compose up -d --build
```

Web: `http://<server>:8080`. Kirish `WEB_ALLOWED_GROUP` guruhidagi domen hisoblari bilan.

Favqulodda lokal admin (AD ishlamay qolgan holat uchun):

```bash
docker compose run --rm api python -m agentmon.api.auth hash   # chiqqan hash'ni WEB_ADMIN_PASSWORD_HASH ga
```

FTD sozlamasi: [deploy/ftd-netflow.md](deploy/ftd-netflow.md).

Yuqori UDP oqimida paketlar yo'qolmasligi uchun serverda UDP qabul buferini oshiring:

```bash
echo 'net.core.rmem_max=33554432' | sudo tee /etc/sysctl.d/90-agentmon.conf && sudo sysctl --system
```

### Servis hisoblari (hammasi faqat o'qish uchun)

| Manba | Kerakli huquq |
|---|---|
| AD | Oddiy domen foydalanuvchisi (kompyuterlar, Sites, DomainDnsZones'ni o'qish). **LDAPS** (636) tavsiya etiladi. |
| Cortex XDR | *Viewer* rolidagi API key (Standard yoki Advanced) |
| KSC | Faqat o'qish huquqli foydalanuvchi, OpenAPI porti 13299 |

## Demo rejimi

Real manbalarni ulashdan oldin interfeysni ko'rish uchun (**faqat bo'sh bazada!**):

```bash
docker compose run --rm engine python -m agentmon.demo --yes
```

Bu buyruq haqiqiy engine kodini soxta 4000 host muhitida ishga tushiradi. Real ishga tushirishdan oldin bazani tozalang:

```bash
docker compose down && docker volume rm agentmon_pg-data
```

## Pilot bo'yicha tekshiruv ro'yxati

Tizim real muhitga ulangach, quyidagilarni tasdiqlash kerak:

1. **NSEL keladi:** *Tizim holati* sahifasida "Oqim" > 0, "flow-update: kelmoqda".
   Logstash'ni FTD'siz sinash uchun: `python3 tools/nsel_sim.py --collector <server> --src 10.10.1.50 --target 172.25.44.50:8888`.
2. **Subnetlar:** *Tizim holati → Foydalanuvchi subnetlari*. AD Sites'da subnetlar bo'lmasa, `.env` dagi `USER_SUBNETS` ga yozing. Server VLAN'lari, printerlar va IP-telefonlarni `EXCLUDE_SUBNETS` ga qo'shing.
3. **Manbalar:** *Inventar manbalari* jadvalida uchala manba "Ishlayapti" holatida va yozuvlar soni kutilganga mos bo'lishi kerak.
4. **KSC maydonlari:** holati ma'lum 2–3 ta kompyuterda (RTP o'chirilgan, bazalar eski) natija to'g'ri ekanini tekshiring. `KLHST_WKS_LAST_UPDATE` va `KLHST_WKS_STATUS` bitlari KSC versiyasiga qarab farq qilishi mumkin.
5. **Chegaralarni kalibrlash:** 20–30 ta holati ma'lum kompyuterni tanlang, jumladan ataylab agenti o'chirilganlarni. Keyin `THRESH_*` qiymatlari to'g'ri ekanini tekshiring. Buning uchun host sahifasidagi "oxirgi trafik" qiymati agentning haqiqiy heartbeat oralig'ini ko'rsatadi.
6. **SearchInform porti:** 8090 portga agentlar doimiy murojaat qilishini tasdiqlang. Agar SI agentlari faqat faollik bo'lganda ma'lumot yuborsa, `THRESH_SI` ni oshiring.

## Ishlab chiqish

```bash
cd backend && pip install -r requirements.txt pytest pytest-asyncio pgserver && pytest   # 69 ta test
cd web && npm install && API_URL=http://localhost:8000 npm run dev
```

PostgreSQL talab qiladigan testlar (engine integratsiyasi va API) `pgserver` paketi yoki `AGENTMON_TEST_DSN` orqali ishlaydi. Ikkalasi ham bo'lmasa, bu testlar o'tkazib yuboriladi.

Logstash pipeline'ni haqiqiy Logstash 8.15 va `tools/nsel_sim.py` bilan sinab ko'rilgan: codec maydon nomlari mos keladi, internetdan kelgan manbalar filtrlanadi.

| Fayl | Nima bor |
|---|---|
| `backend/agentmon/engine/rules.py` | Holat qoidalari (sof funksiyalar) |
| `backend/agentmon/engine/signals.py` | NSEL hodisalari → xotiradagi signallar |
| `backend/agentmon/engine/classify.py` | Subnet va target tasnifi |
| `backend/agentmon/engine/identity.py` | IP → host moslash |
| `backend/agentmon/engine/inventory/` | AD, Cortex, KSC klientlari |
| `backend/agentmon/engine/app.py` | Engine orkestratsiyasi |
| `backend/agentmon/api/` | REST API va autentifikatsiya |
| `web/src/` | React interfeys |

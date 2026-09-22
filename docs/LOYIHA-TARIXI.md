# AgentMon: loyiha tarixi, talablar va qarorlar

Bu hujjatda loyihaning butun konteksti saqlangan: vazifa qanday qo'yilgan, qanday ma'lumotlar berilgan,
qanday tahlil qilingan va nima uchun aynan shunday qarorlar qabul qilingan.
Oxirgi yangilanish: 2026-09-22.

---

## 1. Vazifa

Korxonada **~4000 ta kompyuter** bor. Ularga **Cortex XDR**, **Kaspersky** (KSC orqali boshqariladi),
**SearchInform** (DLP) va shunga o'xshash xavfsizlik dasturlari o'rnatilgan.

**Savol:** qaysi kompyuterlarda bu dasturlar o'rnatilmagan, o'chirilgan yoki ishlamayapti?
Qo'shimcha: kompyuter **AD'ga ulanganmi** (domen a'zosimi)?

Talab: shoshilmasdan, chuqur o'ylab, senior darajasida professional yechim.

---

## 2. Berilgan ma'lumotlar (muhit)

| Savol | Javob |
|---|---|
| Ma'lumot manbai | Tarmoq orqali barcha kompyuterlarning servislarga ulanishi haqidagi **NetFlow** olinadi. Kompyuter servisga ulanayotgan bo'lsa, u lokal tarmoqda. |
| NetFlow manbai | **Cisco FTD**, NetFlow **v9** (NSEL) |
| Kollektor | **Logstash** ishlatish rejalashtirilgan |
| VLAN'lar orasidagi routing | **FTD'da**. Foydalanuvchi va server VLAN'lari alohida. |
| Filiallar | Filiallar trafigining NetFlow'i ham bor. Mahalliy DC yoki KSC distribution point **yo'q**, hamma **bosh serverlarga** ulanadi. |
| Cortex XDR | Agentlar **Broker VM** orqali chiqadi: `172.25.44.50:8888` |
| Kaspersky | KSC serveri: `172.25.25.111` (agent portlari 13000/14000, OpenAPI 13299) |
| SearchInform | `172.25.43.205:8090` |
| DC va DHCP loglari | **Mavjud emas** |
| Elasticsearch | Bor, lekin **SIEM vazifasini bajaradi**, shuning uchun unga tegilmaydi |
| Cortex API kaliti | Olish mumkin (faqat o'qish uchun) |
| Kaspersky | Tekshiruvga **kiradi** |
| Server | **Ubuntu**, **Docker** |
| Natija | Faqat **web interfeys** (dashboard). Telegram yoki email alertlar kerak emas. |
| Tezlik | "Engine tez ishlasa bo'ldi" |

---

## 3. Tahlil: asosiy g'oyalar

### 3.1. O'rnatilmagan agent o'zi haqida xabar bermaydi
Faqat konsollarga (KSC, Cortex) qarash yetarli emas: agent o'rnatilmagan kompyuter konsolda umuman ko'rinmaydi.
Shuning uchun tizim **solishtirish (reconciliation)** asosida quriladi:

```
Muammoli kompyuterlar = [Tarmoqda tirik kompyuterlar] − [Agenti serverga murojaat qilayotganlar]
```

### 3.2. Ikki mustaqil dalil (triangulation)
- **Tarmoq dalili (NSEL):** agent o'z serveriga murojaat qilyaptimi? Bu dalilni endpoint tarafidan aldab bo'lmaydi.
  Foydalanuvchi agentni o'chirsa, trafik to'xtaydi.
- **Konsol dalili (API):** AD, Cortex va KSC hostni biladimi, himoya yoqilganmi, bazalar yangimi.

### 3.3. Signal xaritasi
| Mahsulot | Tarmoq signali | Izoh |
|---|---|---|
| AD | DC'larga **88 (Kerberos), 389 (LDAP), 445 (SMB/GPO)** | DNS (53) hisobga olinmaydi: domendan tashqaridagi qurilmalar ham DC'ni DNS sifatida ishlatadi |
| Cortex | `172.25.44.50:8888` | Broker VM bo'lgani uchun cloud IP'lari o'zgarishi muammosi yo'q |
| KSC | `172.25.25.111:13000/14000` | |
| SI | `172.25.43.205:8090` | Konsol API'si ishlatilmaydi, faqat tarmoq |

### 3.4. Aniqlangan qiyin joylar va ularning yechimlari
| Muammo | Yechim |
|---|---|
| IP ≠ kompyuter (DHCP), DC/DHCP loglari yo'q | IP → host moslash uchun AD-integrated DNS (LDAP orqali `dnsRecord`), Cortex API va KSC API'dagi agent IP'lari birlashtiriladi. Eng yangi dalil g'olib bo'ladi, qo'shimcha himoya qoidalari bor (5-bo'lim). |
| FTD NSEL hodisaga asoslangan: uzoq ochiq ulanishlar soatlab "jim" | FTD FlexConfig'da `flow-export active refresh-interval 5` **majburiy** (`deploy/ftd-netflow.md`) |
| "Agent yo'q" va "kompyuter o'chiq"ni ajratish | Hukm faqat tirik hostlar uchun chiqariladi. Oflayn bo'lsa, oxirgi ma'lum holat saqlanadi. |
| Kompyuter endigina yoqilgan | Grace period: salbiy xulosa faqat host yetarlicha uzoq tirik bo'lgandan keyin chiqariladi |
| Broker VM yoki server tushib qolsa, minglab soxta alert | Ommaviy uzilishni aniqlash: oldin OK bo'lgan hostlarning ≥20% qismi birdan jim bo'lsa, bitta insident chiqariladi va xulosalar ushlab turiladi |
| Kollektor to'xtasa, "hamma jim" | Kollektor sog'lig'i kuzatiladi, holatlar muzlatiladi |
| Printer, telefon va serverlar | Subnet va IP istisnolari, web'da "Ma'lum qurilma" belgilash |
| Begona qurilmalar | Foydalanuvchi subnet'ida faol, lekin inventarda yo'q IP'lar "Noma'lum qurilmalar" ro'yxatiga tushadi |
| Kompyuter bo'lmagan server trafigi | Faqat foydalanuvchi subnetlaridan chiqqan trafik hisobga olinadi (AD Sites & Services'dan avtomatik) |

---

## 4. Arxitektura qarorlari (ADR)

| # | Qaror | Sababi |
|---|---|---|
| 1 | SIEM'dagi Elasticsearch **ishlatilmaydi** | SIEM kritik tizim. Bizga flow'larni saqlash kerak emas, butun holat `last_seen[(ip, guruh)]` jadvaliga sig'adi (~20 ming yozuv, xotirada). |
| 2 | **Logstash → Redis → Python engine** | Logstash faqat dekodlaydi (stateless). Redis bufer vazifasini bajaradi: engine qayta ishga tushsa, hodisalar yo'qolmaydi. Logika Python'da, u yerda test qilinadi. |
| 3 | **PostgreSQL** | Inventar, holatlar, tarix, statistika |
| 4 | **FastAPI + React** web interfeys (Grafana emas) | Foydalanuvchi web interfeys so'radi |
| 5 | FTD `refresh-interval` majburiy | Busiz ishlab turgan agentlar ham "jim" ko'rinadi |
| 6 | Hisob-kitob **hodisalar vaqti** (watermark) bo'yicha | Engine orqada qolsa ham hostlar "jim" bo'lib ketmaydi |
| 7 | Kirish AD guruhi orqali (LDAP) + favqulodda lokal admin | Xavfsizlik vositasi ochiq bo'lmasligi kerak |
| 8 | Hostlarni birlashtirish kaliti — **NetBIOS nomi** (15 belgi, `sAMAccountName`) | AD/KSC qisqa nomni, Cortex/DNS to'liq hostname'ni beradi. Domenda aynan NetBIOS nomi noyob. To'qnashuvda (domensiz qurilmalar) to'liq nom qoldiriladi |
| 9 | LDAP login: **avval qidirish (servis hisobi), keyin topilgan DN bilan bind** | Ruxsat tekshirilgan hisob va parol tekshirilgan hisob doim bitta (ishonchli domendagi hamnom huquq ololmaydi) |
| 10 | Ikki rol: kuzatuvchi / administrator (`WEB_ADMIN_GROUP`) + audit jurnali | Xodim o'z qurilmasini "istisno" qilib yashira olmasligi, har o'zgarish izlanishi uchun |
| 11 | HTTPS web konteyner ichida (sertifikat yo'q bo'lsa o'z-o'zidan imzolangan) | AD parollari hech qachon ochiq kanalda yuborilmasligi uchun — "keyin sozlanadi" qadamiga qoldirilmaydi |
| 12 | NetFlow faqat `NSEL_EXPORTERS` dan (Logstash + engine) va `DOCKER-USER` firewall | UDP autentifikatsiyasiz: soxta NetFlow bilan agentsiz kompyuterni "OK" qilib ko'rsatish mumkin. Docker portlari ufw'ni chetlab o'tadi |

**Unumdorlik (o'lchangan):** ingest bitta yadroda sekundiga ~256 ming hodisa (kutilgan yuklama 5–10 ming). 4000 hostni baholash ~0.5 soniya.

---

## 5. Holatlar va qoidalar

| Holat | Shart |
|---|---|
| OK (Ishlayapti) | Trafik bor, konsol sog'lom |
| UNHEALTHY (Himoya to'liq emas) | Trafik bor, lekin konsol muammo ko'rsatmoqda (RTP o'chiq, bazalar eski, qisman himoya, KES yo'q). **Yoki** FTD agent trafigini rad etmoqda. |
| STOPPED (To'xtatilgan) | Konsolda bor, host tirik, lekin serverga trafik yo'q |
| NOT_INSTALLED (O'rnatilmagan) | Konsolda yo'q va trafik yo'q |
| NO_SIGNAL (Signal yo'q) | Trafik yo'q, konsol ma'lumoti yo'q (SI yoki manba eskirgan) |
| CONFLICT (Ziddiyat) | Tarmoq va konsol bir-biriga zid |
| PENDING (Tekshirilmoqda) | Host yaqinda yoqilgan |
| OFFLINE (Oflayn) | Host tarmoqda ko'rinmayapti, oxirgi ma'lum holat saqlanadi |

**Boshlang'ich chegaralar (pilotda kalibrlanadi):** tirik oynasi 10 daqiqa, grace 30 daqiqa, AD 4 soat, Cortex 30 daqiqa,
KSC 45 daqiqa, SI 1 soat, KSC bazalari 72 soat, debounce 2 marta, ommaviy uzilish ≥20% (kamida 20 host).

**IP moslash qoidalari (DHCP xavfiga qarshi):**
1. Eng yangi dalil g'olib. Vaqt teng bo'lsa, agent dalili DNS'dan ustun.
2. Agent hostning yangi IP'sini xabar qilgan bo'lsa, o'sha hostning boshqa IP'lardagi eski dalillari bekor qilinadi.
3. **Faol** IP'ning dalili (DNS yoki agent) shu IP'ning **joriy faollik seansidan oldin** olingan bo'lsa, u oldingi egasiga
   tegishli bo'lishi mumkin (kechqurun laptop ketdi, ertalab DHCP IP'ni boshqa qurilmaga berdi). Bunday dalil joriy seansdagi
   trafik bilan tasdiqlanishi shart: DNS uchun — DC bilan **Kerberos/LDAP** (88/389; 445 emas — domensiz qurilma ham NTLM
   bilan ulana oladi), agent uchun — shu yoki Cortex/KSC serveriga trafik. Aks holda IP noma'lum qurilma sifatida
   ko'rsatiladi ("oldin: <kompyuter>" ishorasi bilan) va oldingi egasi noto'g'ri ayblanmaydi.

---

## 6. Amalga oshirilgan ishlar

**Birinchi bosqich: qurish.**
- Backend: engine, inventar klientlari (AD, DNS, Cortex, KSC), qoidalar, API va autentifikatsiya.
- Web: umumiy ko'rinish, kompyuterlar, host kartochkasi, noma'lum qurilmalar, tizim holati. O'zbek tilida, yorug' va qorong'i mavzu, CSV eksport.
- Logstash pipeline, docker-compose, NSEL simulyatori (`tools/nsel_sim.py`), demo rejimi (`python -m agentmon.demo --yes`) va hujjatlar.

**Ikkinchi bosqich: to'liq audit.** Topilgan va tuzatilgan muammolar:
- Restartdan yoki NSEL uzilishidan keyingi "hamma oflayn" to'lqini. Isinish davri qo'shildi.
- DHCP tufayli noto'g'ri host ayblanishi. IP moslash qoidalari 2 va 3 qo'shildi.
- `PENDING` holati "oxirgi ma'lum holat"ni o'chirib yuborardi.
- DB yozuvi muvaffaqiyatsiz bo'lsa holat yo'qolardi. Endi holat faqat DB'ga yozilgandan keyin xotiraga olinadi.
- FTD bloklagan agent "o'rnatilmagan" deb ko'rsatilardi. Rad etilgan urinishlar endi alohida yoziladi.
- Logstash Ruby konstantasi xavfi tuzatildi.
- Qo'shimcha: bitta engine nusxasi (advisory lock), HTTP qayta urinishlar, LDAP referral'lar o'chirildi, inventar keskin kamayganda uch marta tasdiqlanadi, istisnolar darhol qo'llanadi, retention qo'shildi, parol `.env` da bitta joyda.
- Xavfsizlik: CSV injection'dan himoya, IP validatsiyasi, `WEB_SECRET` ishga tushishda tekshiriladi, nginx header'lari tuzatildi.

**Tekshiruvlar:**
- 69 ta avtomatik test, jumladan haqiqiy PostgreSQL bilan engine va API testlari.
- Haqiqiy **Logstash 8.15.3** va simulyator bilan uchdan-uchgacha sinov: codec maydon nomlari (`ipv4_src_addr`, `ipv4_dst_addr`, `l4_dst_port`, `fw_event` — ham 233, ham 40005) tasdiqlandi.

---

**Uchinchi bosqich: ikkinchi to'liq audit (2026-09-22).** Topilgan va tuzatilgan muammolar:
- **IP moslash (DHCP):** agent xabar bergan eski IP tekshirilmas edi — kecha ketgan laptop "to'xtatilgan" deb ayblanib,
  bugun shu IP'ni olgan begona qurilma yashirinardi. Kechagi DNS yozuvida ham xuddi shu teshik bor edi. 3-qoida seansga
  asoslangan qilib qayta yozildi; domen a'zoligi faqat Kerberos/LDAP trafigi bilan isbotlanadi.
- **Nomlar:** 15 belgidan uzun hostname'li kompyuter ikkita host bo'lib ketardi (AD/KSC NetBIOS, Cortex to'liq nom). ADR 8.
- **Tarmoq:** Docker portlari ufw'ni chetlab o'tardi, web HTTP'da edi (AD parollari ochiq), NetFlow porti hammaga ochiq
  va soxtalashtirish mumkin edi. ADR 11–12, `deploy/docker-firewall.sh`.
- **Login:** boshqa domendagi hamnom huquqi (ADR 9); urinishlarni cheklash nginx IP'si bo'yicha ishlardi (istalgan odam
  adminni bloklay olardi) — endi haqiqiy mijoz IP'si, PostgreSQL'da, faqat xato urinishlar.
- **Rollar va audit** (ADR 10).
- **Ommaviy uzilish** cheksiz "OK"da ushlab turardi va kechasi yoki holat o'zgarganda noto'g'ri yopilardi. Endi bazaviy
  hostlar insidentda saqlanadi, `MASS_OUTAGE_MAX_HOLD` (4 soat) dan keyin eskalatsiya.
- **Har bir FTD alohida kuzatiladi:** keskin jimlik — insident, filial kechasi yopilishi — insident emas.
- **Redis bufer** o'lchandi: ~105 bayt/hodisa, 1 GB ≈ 10 ming/s da 17 daqiqa (ADR 2'dagi "yo'qolmaydi" faqat shu oraliqda
  to'g'ri). `REDIS_MAXMEMORY` sozlanadi, bandlik UI'da.
- **Sirlar va TLS:** namunadagi parol/kalit olib tashlandi va rad etiladi; `AD_CA_FILE`, `KSC_CA_FILE`.
- **Barqarorlik:** engine watchdog (event loop osilsa — restart), advisory lock yo'qolsa to'xtash, healthcheck'lar,
  log rotatsiyasi, API keshi worker'lar orasida sinxron, oflayn/tekshirilmoqda tarixi 30 kun.
- **Testlar:** 110 ta (DB testlari ham), `tools/test.sh` (faqat Docker), GitHub Actions CI (Logstash konfiguratsiyasi ham).

## 7. Hali tasdiqlanmagan va keyingi qadamlar

1. **`docker compose up`** ishlab chiqish kompyuterida alohida muhitda to'liq sinaldi; haqiqiy serverda hali ishga tushirilmagan.
   `deploy/docker-firewall.sh` faqat izolyatsiyalangan konteynerda sinalgan — serverda `--show` bilan tekshiring.
2. **FTD sozlamasi:** `deploy/ftd-netflow.md` bo'yicha, `refresh-interval` bilan.
3. **Haqiqiy AD, Cortex va KSC'ga ulanish.** Ayniqsa KSC maydonlari (`KLHST_WKS_STATUS` bitlari, `KLHST_WKS_LAST_UPDATE`) 2–3 ta holati ma'lum kompyuterda tasdiqlanishi kerak.
4. **Pilot:** 1 ta VLAN, 20–30 ta holati ma'lum kompyuter. `THRESH_*` chegaralarini kalibrlash. SearchInform agentlarining 8090 portga murojaat davriyligini tekshirish.
5. Keyinchalik ixtiyoriy yaxshilash: DC'larga Winlogbeat qo'yib, 4768-hodisani yig'ish (IP → host moslash yanada aniqroq bo'ladi). Holat o'zgarishlarini SIEM'ga yuborish.

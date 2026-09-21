# Cisco FTD: NSEL (NetFlow v9) eksportini sozlash

AgentMon'ga FTD'dan o'tadigan **barcha** sessiyalar haqidagi NSEL hodisalari kerak:
foydalanuvchi VLAN'laridan DC, Broker VM, KSC va SearchInform serverlariga, hamda internetga.

## 1. FlexConfig (FMC)

FMC versiyangizda **Devices → Platform Settings → NetFlow** bo'limi bo'lsa, o'shani ishlating.
Bo'lmasa, **Objects → FlexConfig → FlexConfig Object** yarating (Deployment: *Everytime*, Type: *Append*):

```
flow-export destination <ICHKI_INTERFEYS_NOMI> <AGENTMON_SERVER_IP> 2055
flow-export template timeout-rate 1
flow-export active refresh-interval 5
policy-map global_policy
 class class-default
  flow-export event-type all destination <AGENTMON_SERVER_IP>
```

Obyektni **Devices → FlexConfig** policy'ga qo'shing, uni barcha FTD'larga (markaz va filiallar) biriktiring, keyin Deploy qiling.

| Buyruq | Nima uchun kerak |
|---|---|
| `flow-export destination` | NSEL qayerga yuborilishi. Interfeys AgentMon serveriga yo'l bo'lgan interfeys bo'lishi kerak. |
| `template timeout-rate 1` | Template har daqiqada qayta yuboriladi, shuning uchun Logstash qayta ishga tushganda ham tez tiklanadi. |
| `active refresh-interval 5` | **Majburiy.** Uzoq ochiq turadigan ulanishlar (KSC 13000, Cortex Broker) uchun har 5 daqiqada `flow-update` yuboriladi. Busiz ishlab turgan agent ham "jim" ko'rinadi. |
| `event-type all` | created / teardown / denied / update hodisalarining barchasi. |

## 2. Tekshirish

FTD CLI'da (`system support diagnostic-cli`):

```
show flow-export counters
show running-config flow-export
```

`show flow-export counters` natijasida `packets sent` soni oshib borishi va xatolar 0 bo'lishi kerak.

AgentMon web interfeysida **Tizim holati → NetFlow (NSEL) kollektori** bo'limida:
- **Oqim** 0 dan katta bo'lishi kerak;
- **flow-update** qatorida "kelmoqda" yozuvi chiqishi kerak (refresh-interval ishlayotganining belgisi).

## 3. Firewall qoidasi

AgentMon serveriga UDP/2055 ga barcha FTD'lardan (filiallar ham) kirishga ruxsat bering.

## 4. Muhim: inter-VLAN trafik

Tizim foydalanuvchi VLAN'laridan server VLAN'lariga boradigan trafikni ko'rishi kerak.
Siz aytganingizdek, routing FTD'da bo'lgani uchun bu trafik FTD'dan o'tadi.
Agar biror filialda routing L3 switch'da bo'lsa, o'sha switch'da ham Flexible NetFlow v9 yoqish kerak.

-- Idempotent sxema: engine har ishga tushganda qo'llaydi.

-- Kanonik kompyuterlar ro'yxati (AD + konsollar birlashmasi).
CREATE TABLE IF NOT EXISTS host (
    id           bigserial PRIMARY KEY,
    name         text NOT NULL UNIQUE,            -- normallashtirilgan qisqa nom (kichik harf)
    display_name text NOT NULL,
    fqdn         text,
    os           text,
    site         text,
    sources      text[] NOT NULL DEFAULT '{}',    -- qaysi manbalarda bor: ad, cortex, ksc
    excluded     boolean NOT NULL DEFAULT false,
    note         text,
    first_seen   timestamptz NOT NULL DEFAULT now(),
    last_alive   timestamptz,
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- Har bir konsol (va AD) ko'rinishi, normallashtirilgan.
CREATE TABLE IF NOT EXISTS console_endpoint (
    product      text NOT NULL,
    name         text NOT NULL,
    display_name text,
    fqdn         text,
    os           text,
    ips          inet[] NOT NULL DEFAULT '{}',
    last_seen    timestamptz,
    healthy      boolean NOT NULL,
    reason       text,
    version      text,
    details      jsonb NOT NULL DEFAULT '{}',
    synced_at    timestamptz NOT NULL,
    PRIMARY KEY (product, name)
);
CREATE INDEX IF NOT EXISTS console_endpoint_ips_idx ON console_endpoint USING gin (ips);

-- AD-integrated DNS A-yozuvlari (IP -> host moslash uchun).
CREATE TABLE IF NOT EXISTS dns_record (
    ip          inet NOT NULL,
    name        text NOT NULL,
    fqdn        text,
    observed_at timestamptz,          -- NULL = statik yozuv
    PRIMARY KEY (ip, name)
);

CREATE TABLE IF NOT EXISTS net_subnet (
    cidr   cidr PRIMARY KEY,
    site   text NOT NULL,
    source text NOT NULL              -- ad | config
);

CREATE TABLE IF NOT EXISTS dc_server (
    ip   inet PRIMARY KEY,
    name text
);

CREATE TABLE IF NOT EXISTS source_status (
    source       text PRIMARY KEY,
    last_attempt timestamptz,
    last_success timestamptz,
    last_error   text,
    item_count   integer
);

-- Tarmoq signallari (engine xotirasidagi holatning nusxasi).
CREATE TABLE IF NOT EXISTS ip_presence (
    ip          inet PRIMARY KEY,
    site        text,
    first_seen  timestamptz NOT NULL,
    alive_since timestamptz NOT NULL,
    last_seen   timestamptz NOT NULL
);
ALTER TABLE ip_presence ADD COLUMN IF NOT EXISTS exporter text;   -- IP oxirgi marta qaysi FTD orqali ko'ringan

CREATE TABLE IF NOT EXISTS net_signal (
    ip        inet NOT NULL,
    grp       text NOT NULL,
    last_seen timestamptz NOT NULL,
    PRIMARY KEY (ip, grp)
);

-- Joriy IP -> host moslik.
CREATE TABLE IF NOT EXISTS host_ip (
    ip           inet PRIMARY KEY,
    host_id      bigint NOT NULL REFERENCES host(id) ON DELETE CASCADE,
    source       text NOT NULL,
    observed_at  timestamptz,
    alternatives integer NOT NULL DEFAULT 0,
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS host_ip_host_idx ON host_ip (host_id);

CREATE TABLE IF NOT EXISTS host_state (
    host_id           bigint NOT NULL REFERENCES host(id) ON DELETE CASCADE,
    product           text NOT NULL,
    state             text NOT NULL,
    reason            text,
    since             timestamptz NOT NULL,
    last_known        text,              -- OFFLINE bo'lganda oxirgi ma'lum holat
    last_known_reason text,
    net_last_seen     timestamptz,
    console_last_seen timestamptz,
    evaluated_at      timestamptz NOT NULL,
    PRIMARY KEY (host_id, product)
);

CREATE TABLE IF NOT EXISTS host_state_history (
    id         bigserial PRIMARY KEY,
    host_id    bigint NOT NULL REFERENCES host(id) ON DELETE CASCADE,
    product    text NOT NULL,
    state      text NOT NULL,
    reason     text,
    started_at timestamptz NOT NULL,
    ended_at   timestamptz
);
CREATE INDEX IF NOT EXISTS hsh_host_idx ON host_state_history (host_id, started_at DESC);

CREATE TABLE IF NOT EXISTS incident (
    id         bigserial PRIMARY KEY,
    kind       text NOT NULL,             -- mass_outage | collector_stale | exporter_stale
    product    text,
    started_at timestamptz NOT NULL,
    ended_at   timestamptz,
    details    jsonb NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS coverage_snapshot (
    ts      timestamptz NOT NULL,
    product text NOT NULL,
    state   text NOT NULL,
    count   integer NOT NULL,
    PRIMARY KEY (ts, product, state)
);

CREATE TABLE IF NOT EXISTS system_status (
    key        text PRIMARY KEY,
    value      jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Noma'lum qurilmalar ro'yxatidan yashiriladigan IP'lar (printer, telefon...).
CREATE TABLE IF NOT EXISTS ip_ignore (
    ip         inet PRIMARY KEY,
    note       text,
    created_by text,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Muvaffaqiyatsiz login urinishlari (cheklash uchun; 1 kundan eskilari o'chiriladi).
CREATE TABLE IF NOT EXISTS login_failure (
    ts       timestamptz NOT NULL DEFAULT now(),
    ip       text NOT NULL,
    username text NOT NULL
);
CREATE INDEX IF NOT EXISTS login_failure_ip_idx ON login_failure (ip, ts);

-- Audit jurnali: kim, qachon, qayerdan, nimani o'zgartirdi (istisno, IP yashirish, kirish).
CREATE TABLE IF NOT EXISTS audit_log (
    id       bigserial PRIMARY KEY,
    ts       timestamptz NOT NULL DEFAULT now(),
    username text NOT NULL,
    ip       text,
    action   text NOT NULL,
    target   text,
    details  jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS audit_log_ts_idx ON audit_log (ts DESC);

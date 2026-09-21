"""Umumiy fixture'lar.

PostgreSQL talab qiladigan testlar uchun manba (birinchisi topilgani):
  - AGENTMON_TEST_DSN muhit o'zgaruvchisi (masalan, docker'dagi test bazasi);
  - `pgserver` paketi (pip install pgserver) — vaqtinchalik lokal server.
Hech biri bo'lmasa, bu testlar o'tkazib yuboriladi.
"""

import os

import pytest


@pytest.fixture(scope="session")
def dsn(tmp_path_factory):
    if os.environ.get("AGENTMON_TEST_DSN"):
        return os.environ["AGENTMON_TEST_DSN"]
    pgserver = pytest.importorskip("pgserver")
    srv = pgserver.get_server(str(tmp_path_factory.mktemp("pg")), cleanup_mode="stop")
    return srv.get_uri()

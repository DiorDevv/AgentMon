from agentmon.engine.rules import (
    C_MISSING,
    C_NONE,
    C_PRESENT,
    C_UNKNOWN,
    ConsoleEvidence,
    Evidence,
    apply_debounce,
    effective_state,
    evaluate,
)
from agentmon.model import CONFLICT, NO_SIGNAL, NOT_INSTALLED, OFFLINE, OK, PENDING, STOPPED, UNHEALTHY

NOW = 1_000_000
T = 1800
GRACE = 1800


def ev(alive=True, alive_for=10_000, net_ago=60, console=ConsoleEvidence(C_PRESENT)):
    net_last = None if net_ago is None else NOW - net_ago
    return Evidence(NOW, alive, alive_for, net_last, T, GRACE, console)


def test_offline_host_is_not_judged():
    assert evaluate(ev(alive=False, net_ago=None))[0] == OFFLINE


def test_ok_when_traffic_and_console_healthy():
    assert evaluate(ev())[0] == OK


def test_unhealthy_when_console_reports_problem():
    state, reason = evaluate(ev(console=ConsoleEvidence(C_PRESENT, healthy=False, reason="RTP to'xtatilgan")))
    assert state == UNHEALTHY and reason == "RTP to'xtatilgan"


def test_conflict_when_traffic_but_missing_in_console():
    assert evaluate(ev(console=ConsoleEvidence(C_MISSING)))[0] == CONFLICT


def test_product_without_console_is_ok_on_traffic():
    assert evaluate(ev(console=ConsoleEvidence(C_NONE)))[0] == OK


def test_pending_until_host_alive_long_enough():
    assert evaluate(ev(alive_for=600, net_ago=None))[0] == PENDING
    # threshold > grace bo'lsa, threshold kutiladi
    e = Evidence(NOW, True, 3000, None, 14400, GRACE, ConsoleEvidence(C_PRESENT))
    assert evaluate(e)[0] == PENDING


def test_silent_and_missing_is_not_installed():
    assert evaluate(ev(net_ago=None, console=ConsoleEvidence(C_MISSING)))[0] == NOT_INSTALLED


def test_silent_and_present_is_stopped():
    c = ConsoleEvidence(C_PRESENT, last_seen=NOW - 86400)
    assert evaluate(ev(net_ago=T + 1, console=c))[0] == STOPPED


def test_silent_but_console_fresh_is_conflict():
    c = ConsoleEvidence(C_PRESENT, last_seen=NOW - 60)
    assert evaluate(ev(net_ago=T + 1, console=c))[0] == CONFLICT


def test_ad_console_freshness_not_used():
    c = ConsoleEvidence(C_PRESENT, last_seen=NOW - 60, track_fresh=False)
    assert evaluate(ev(net_ago=None, console=c))[0] == STOPPED


def test_silent_without_console_data_is_no_signal():
    assert evaluate(ev(net_ago=None, console=ConsoleEvidence(C_NONE)))[0] == NO_SIGNAL
    assert evaluate(ev(net_ago=None, console=ConsoleEvidence(C_UNKNOWN)))[0] == NO_SIGNAL


def test_stale_console_does_not_create_false_not_installed():
    state, reason = evaluate(ev(console=ConsoleEvidence(C_UNKNOWN)))
    assert state == OK and reason


# ---------------------------------------------------------------- debounce
def test_first_observation_committed_immediately():
    t, changed = apply_debounce(None, STOPPED, "x", NOW, 2)
    assert changed and t.state == STOPPED and t.last_known == STOPPED


def test_problem_state_requires_confirmation():
    t, _ = apply_debounce(None, OK, None, NOW, 2)
    t, changed = apply_debounce(t, STOPPED, "x", NOW + 60, 2)
    assert not changed and t.state == OK
    t, changed = apply_debounce(t, STOPPED, "x", NOW + 120, 2)
    assert changed and t.state == STOPPED and t.since == NOW + 120


def test_flapping_resets_pending():
    t, _ = apply_debounce(None, OK, None, NOW, 2)
    t, _ = apply_debounce(t, STOPPED, "x", NOW + 60, 2)
    t, _ = apply_debounce(t, OK, None, NOW + 120, 2)
    t, changed = apply_debounce(t, STOPPED, "x", NOW + 180, 2)
    assert not changed and t.state == OK


def test_recovery_is_immediate():
    t, _ = apply_debounce(None, STOPPED, "x", NOW, 2)
    t, changed = apply_debounce(t, OK, None, NOW + 60, 2)
    assert changed and t.state == OK


def test_offline_keeps_last_known_state():
    t, _ = apply_debounce(None, NOT_INSTALLED, "yo'q", NOW, 2)
    t, changed = apply_debounce(t, OFFLINE, None, NOW + 60, 2)
    assert changed and t.state == OFFLINE
    assert t.last_known == NOT_INSTALLED and effective_state(t) == NOT_INSTALLED
    t, _ = apply_debounce(t, PENDING, None, NOW + 120, 2)
    assert effective_state(t) == NOT_INSTALLED


def test_reason_change_reported_without_state_change():
    t, _ = apply_debounce(None, UNHEALTHY, "a", NOW, 2)
    t, changed = apply_debounce(t, UNHEALTHY, "b", NOW + 60, 2)
    assert changed and t.reason == "b" and t.since == NOW


def test_pending_does_not_overwrite_last_known():
    t, _ = apply_debounce(None, NOT_INSTALLED, "yo'q", NOW, 2)
    t, _ = apply_debounce(t, OFFLINE, None, NOW + 60, 2)
    t, _ = apply_debounce(t, PENDING, None, NOW + 120, 2)
    t, _ = apply_debounce(t, PENDING, None, NOW + 180, 2)   # bir xil holat takrorlandi
    assert t.last_known == NOT_INSTALLED and effective_state(t) == NOT_INSTALLED


def test_first_pending_has_no_last_known():
    t, _ = apply_debounce(None, PENDING, None, NOW, 2)
    assert t.last_known is None


def test_debounce_does_not_mutate_input():
    t, _ = apply_debounce(None, OK, None, NOW, 2)
    t2, changed = apply_debounce(t, STOPPED, "x", NOW + 60, 2)
    assert not changed and t.pending_state is None and t2.pending_state == STOPPED


def test_denied_traffic_is_unhealthy_not_missing():
    e = Evidence(NOW, True, 10_000, None, T, GRACE, ConsoleEvidence(C_MISSING), net_denied=NOW - 60)
    state, reason = evaluate(e)
    assert state == UNHEALTHY and "FTD" in reason


def test_old_denied_is_ignored():
    e = Evidence(NOW, True, 10_000, None, T, GRACE, ConsoleEvidence(C_MISSING), net_denied=NOW - T - 1)
    assert evaluate(e)[0] == NOT_INSTALLED

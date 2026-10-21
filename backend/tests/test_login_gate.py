from __future__ import annotations

import pytest

from app.login_gate import LoginGate, LoginLocked


def test_locks_after_threshold_and_reports_retry_after() -> None:
    gate = LoginGate(max_failures=3, window_seconds=60, lockout_seconds=120)
    for _ in range(3):
        gate.fail("1.2.3.4")
    with pytest.raises(LoginLocked) as error:
        gate.check("1.2.3.4")
    assert error.value.retry_after > 0


def test_key_is_source_not_username() -> None:
    gate = LoginGate(max_failures=2, window_seconds=60, lockout_seconds=60)
    gate.fail("9.9.9.9")
    gate.fail("9.9.9.9")
    with pytest.raises(LoginLocked):
        gate.check("9.9.9.9")
    # 其他来源不受影响
    gate.check("8.8.8.8")


def test_loopback_and_whitelist_are_exempt() -> None:
    gate = LoginGate(max_failures=1, window_seconds=60, lockout_seconds=60, whitelist="10.0.0.0/8")
    gate.fail("127.0.0.1")
    gate.check("127.0.0.1")
    assert gate.is_exempt("::1")
    gate.fail("10.1.2.3")
    gate.check("10.1.2.3")
    assert gate.is_exempt("10.1.2.3")
    assert not gate.is_exempt("11.1.2.3")


def test_succeed_clears_failures() -> None:
    gate = LoginGate(max_failures=2, window_seconds=60, lockout_seconds=60)
    gate.fail("5.5.5.5")
    gate.succeed("5.5.5.5")
    gate.fail("5.5.5.5")
    gate.check("5.5.5.5")

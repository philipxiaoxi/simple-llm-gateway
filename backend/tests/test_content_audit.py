from __future__ import annotations

import io
import zipfile
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.clock import utcnow
from app.db import get_session_factory
from app.models import ContentAuditFinding, ContentAuditScan, RequestLog, RequestLogMessage
from app.services import content_audit
from app.services.conversation import encode_message_content
from app.services.jobs import JOB_CONTENT_AUDIT

VALID_ID = "110101199003078515"
VALID_CARD = "4111111111111111"
PHONE = "13800138000"
EMAIL = "alice@example.com"
SK = "sk-abcdefghijklmnopqrstuvwxyz123456"
SK_ANT = "sk-ant-abcdefghijklmnopqrstuvwxyz"
BEARER = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9xx"
GHP = "ghp_abcdefghijklmnopqrstuvwxyz1234"
AKIA = "AKIAIOSFODNN7EXAMPLE"
PEM = "-----BEGIN PRIVATE KEY-----"


def write_lexicon(directory: Path) -> Path:
    (directory / "暴恐词库.txt").write_text("爆炸装置\n", encoding="utf-8")
    (directory / "民生词库.txt").write_text("测试敏感词\n重复词\n", encoding="utf-8")
    return directory


def lexicon_zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Sensitive-lexicon-master/暴恐词库.txt", "爆炸装置\n")
        archive.writestr("Sensitive-lexicon-master/民生词库.txt", "测试敏感词\n")
        archive.writestr("Sensitive-lexicon-master/README.md", "skip")
    return buffer.getvalue()


class FakeLexiconClient:
    def __init__(self, payload: bytes | None = None, error: Exception | None = None) -> None:
        self.payload = payload if payload is not None else lexicon_zip_bytes()
        self.error = error
        self.calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, url: str):
        self.calls += 1
        if self.error:
            raise self.error
        return httpx.Response(200, content=self.payload, request=httpx.Request("GET", url))


def make_log(
    *,
    messages: list[dict] | None = None,
    request_body: str | None = None,
    api_key_id: int | None = 7,
    api_key_name: str = "k",
    account_name: str = "DS",
) -> RequestLog:
    session = get_session_factory()()
    try:
        log = RequestLog(
            account_id=1,
            account_name=account_name,
            account_source="upstream",
            api_key_id=api_key_id,
            api_key_name=api_key_name,
            protocol="openai_chat",
            model="deepseek-chat",
            stream=False,
            status="success",
            http_status=200,
            latency_ms=1,
            request_body=request_body,
        )
        session.add(log)
        session.flush()
        for seq, message in enumerate(messages or []):
            session.add(
                RequestLogMessage(
                    log_id=log.id,
                    seq=seq,
                    role=str(message.get("role") or "user"),
                    content_json=encode_message_content(message),
                    created_at=utcnow(),
                )
            )
        session.commit()
        session.refresh(log)
        return log
    finally:
        session.close()


def test_finding_unique_constraint(client: TestClient) -> None:
    log = make_log(messages=[{"role": "user", "content": "hi"}])
    session = get_session_factory()()
    try:
        session.add(
            ContentAuditFinding(
                log_id=log.id,
                message_seq=0,
                category="pii",
                rule_key="phone",
                severity="medium",
                excerpt="13800138000",
                start_offset=0,
                end_offset=11,
            )
        )
        session.commit()
        session.add(
            ContentAuditFinding(
                log_id=log.id,
                message_seq=0,
                category="pii",
                rule_key="phone",
                severity="medium",
                excerpt="13800138000",
                start_offset=0,
                end_offset=11,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
    finally:
        session.rollback()
        session.close()


@pytest.fixture(autouse=True)
def _reset_lexicon_cache() -> None:
    content_audit.reset_content_audit_cache()
    yield
    content_audit.reset_content_audit_cache()


def test_lexicon_missing_directory_degrades(tmp_path: Path) -> None:
    state = content_audit.load_lexicon(tmp_path / "missing", force=True)
    assert state.ok is False
    assert state.automaton is None
    assert "词库未能加载" in (state.error_message or "")
    assert content_audit.detect_sensitive("爆炸装置", state) == []
    hits = content_audit.detect_pii(f"证件 {VALID_ID} 卡号 {VALID_CARD}")
    assert {hit.rule_key for hit in hits} == {"id_card", "bank_card"}


def test_lexicon_loads_categories(tmp_path: Path) -> None:
    state = content_audit.load_lexicon(write_lexicon(tmp_path), force=True)
    assert state.ok is True
    assert "暴恐词库" in state.categories
    assert "民生词库" not in state.categories
    hits = content_audit.detect_sensitive("这里有爆炸装置和测试敏感词", state)
    by_word = {hit.rule_key: hit for hit in hits}
    assert by_word["爆炸装置"].lexicon_category == "暴恐词库"
    assert by_word["爆炸装置"].severity == "high"
    assert "测试敏感词" not in by_word


def test_lexicon_filters_single_char_entries(tmp_path: Path) -> None:
    directory = write_lexicon(tmp_path)
    (directory / "民生词库.txt").write_text("测试敏感词\nb\n屄\n&\n", encoding="utf-8")
    state = content_audit.load_lexicon(directory, force=True)
    assert state.ok is True
    assert state.word_count == 1
    hits = content_audit.detect_sensitive("这里有 b 屄 & 符号和爆炸装置", state)
    by_word = {hit.rule_key: hit for hit in hits}
    assert set(by_word) == {"爆炸装置"}
    assert "b" not in by_word
    assert "屄" not in by_word
    assert "&" not in by_word



def test_extract_scan_text_covers_string_multimodal_and_tools() -> None:
    assert content_audit.extract_scan_text({"content": "纯文本"}) == "纯文本"
    multimodal = content_audit.extract_scan_text(
        {"content": [{"type": "text", "text": "你好"}, {"type": "image", "url": "x"}]}
    )
    assert "你好" in multimodal
    tool = content_audit.extract_scan_text(
        {
            "content": "",
            "tool_calls": [{"id": "1", "function": {"name": "lookup", "arguments": '{"q":"sk-abc"}'}}],
        }
    )
    assert "lookup" in tool


def test_pii_positive_and_negative_examples() -> None:
    text = f"证件{VALID_ID} 卡号{VALID_CARD}"
    hits = {hit.rule_key: hit for hit in content_audit.detect_pii(text)}
    assert hits["id_card"].severity == "high"
    assert hits["bank_card"].severity == "high"
    assert content_audit.detect_pii(f"电话 {PHONE} 邮箱 {EMAIL}") == []
    assert content_audit.detect_pii("手机137000 证件11010119900307851A 卡号1234567890123") == []
    assert content_audit.id_card_checksum_ok("110101199003078516") is False
    assert content_audit.luhn_ok("4111111111111112") is False


def test_secret_patterns() -> None:
    text = f"{SK} {SK_ANT} {BEARER} {PEM} {GHP} {AKIA}"
    keys = {hit.rule_key for hit in content_audit.detect_secrets(text)}
    assert keys == {"sk", "sk-ant", "bearer", "pem", "ghp", "akia"}
    assert content_audit.detect_secrets("sk-short bearer x ghp_short AKIA123") == []


def test_excerpt_dedup_and_mask() -> None:
    text = "前" * 80 + VALID_CARD + "后" * 80
    hits = content_audit.detect_pii(text)
    card = next(hit for hit in hits if hit.rule_key == "bank_card")
    excerpt = content_audit.make_excerpt(text, card.start, card.end)
    assert len(excerpt) <= 120
    assert VALID_CARD in excerpt
    assert content_audit.finding_key(1, 0, card) == (1, 0, "pii", "bank_card", card.start)
    masked = content_audit.mask_excerpt_for_list(excerpt, "pii", "bank_card")
    assert VALID_CARD not in masked
    assert "4111" in masked
    secret_excerpt = f"key={SK}"
    assert SK not in content_audit.mask_excerpt_for_list(secret_excerpt, "secret", "sk")
    assert "sk-a" in content_audit.mask_excerpt_for_list(secret_excerpt, "secret", "sk")


def test_incremental_scan_skips_unchanged_and_picks_new_messages(client: TestClient, tmp_path: Path) -> None:
    lexicon = write_lexicon(tmp_path)
    log = make_log(messages=[{"role": "user", "content": f"密钥 {SK}"}])
    first = content_audit.run_scan_batch_sync(batch_size=50, lexicon_directory=lexicon)
    assert first["processed"] == 1
    assert first["new_findings"] >= 1
    second = content_audit.run_scan_batch_sync(batch_size=50, lexicon_directory=lexicon)
    assert second["processed"] == 0
    assert second["new_findings"] == 0
    session = get_session_factory()()
    try:
        session.add(
            RequestLogMessage(
                log_id=log.id,
                seq=1,
                role="user",
                content_json=encode_message_content({"role": "user", "content": f"卡号 {VALID_CARD}"}),
                created_at=utcnow(),
            )
        )
        session.commit()
    finally:
        session.close()
    third = content_audit.run_scan_batch_sync(batch_size=50, lexicon_directory=lexicon)
    assert third["processed"] == 1
    assert third["new_findings"] >= 1
    session = get_session_factory()()
    try:
        seqs = [
            row.message_seq
            for row in session.scalars(select(ContentAuditFinding).where(ContentAuditFinding.log_id == log.id)).all()
        ]
    finally:
        session.close()
    assert 0 in seqs
    assert 1 in seqs


def test_batch_limit_keeps_remaining(client: TestClient, tmp_path: Path) -> None:
    lexicon = write_lexicon(tmp_path)
    make_log(messages=[{"role": "user", "content": "a"}])
    make_log(messages=[{"role": "user", "content": "b"}])
    make_log(messages=[{"role": "user", "content": "c"}])
    result = content_audit.run_scan_batch_sync(batch_size=2, lexicon_directory=lexicon)
    assert result["processed"] == 2
    assert result["remaining"] == 1


def test_oversized_log_is_skipped(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    lexicon = write_lexicon(tmp_path)
    monkeypatch.setattr(content_audit, "MAX_AUDIT_MESSAGES", 3)
    # 4 条消息（超过上限 3），最后一条含敏感词，若未被跳过就会被扫出
    log = make_log(
        messages=[
            {"role": "user", "content": f"消息 {index}"}
            for index in range(3)
        ]
        + [{"role": "user", "content": f"卡号 {VALID_CARD}"}]
    )
    result = content_audit.run_scan_batch_sync(batch_size=50, lexicon_directory=lexicon)
    assert result["processed"] == 1
    assert result["new_findings"] == 0
    session = get_session_factory()()
    try:
        findings = session.scalars(
            select(ContentAuditFinding).where(ContentAuditFinding.log_id == log.id)
        ).all()
        assert findings == []
        scan = session.get(ContentAuditScan, log.id)
        assert scan is not None
        assert scan.status == "skipped"
    finally:
        session.close()


def test_corrupt_message_is_isolated(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    lexicon = write_lexicon(tmp_path)
    bad = make_log(messages=[{"role": "user", "content": "坏"}, {"role": "user", "content": f"{SK}"}])
    good = make_log(messages=[{"role": "user", "content": f"{VALID_CARD}"}])
    original = content_audit.decode_stored_message

    def boom(row):
        if row.log_id == bad.id and row.seq == 0:
            raise ValueError("损坏")
        return original(row)

    monkeypatch.setattr(content_audit, "decode_stored_message", boom)
    result = content_audit.run_scan_batch_sync(batch_size=50, lexicon_directory=lexicon)
    assert result["processed"] == 2
    session = get_session_factory()()
    try:
        scan = session.get(ContentAuditScan, bad.id)
        assert scan is not None
        assert scan.status == "error"
        assert "损坏" in (scan.error_message or "")
        assert session.get(ContentAuditScan, good.id).status == "ok"
        secrets = session.scalars(
            select(ContentAuditFinding).where(
                ContentAuditFinding.log_id == bad.id, ContentAuditFinding.rule_key == "sk"
            )
        ).all()
        assert secrets
    finally:
        session.close()


def test_lexicon_sync_overwrites_cache_and_records_time(client: TestClient, monkeypatch) -> None:
    fake = FakeLexiconClient()
    monkeypatch.setattr(content_audit.httpx, "Client", lambda **_kwargs: fake)
    first = content_audit.sync_lexicon()
    assert first["ok"] is True
    assert first["updated_at"] is not None
    cache = content_audit.lexicon_dir()
    (cache / "民生词库.txt").write_text("旧词\n", encoding="utf-8")
    fake.payload = lexicon_zip_bytes()
    fake.calls = 0
    again = content_audit.sync_lexicon()
    assert fake.calls >= 1
    assert again["updated_at"] >= first["updated_at"]
    # 民生词库不再是高危词库，同步后应被清理
    assert not (cache / "民生词库.txt").exists()
    assert content_audit.lexicon_updated_at() is not None


def test_lexicon_sync_api(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    fake = FakeLexiconClient()
    monkeypatch.setattr(content_audit.httpx, "Client", lambda **_kwargs: fake)
    denied = client.post("/api/admin/content-audit/lexicon/sync")
    assert denied.status_code == 401
    response = client.post("/api/admin/content-audit/lexicon/sync", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["updated_at"]
    assert body["word_count"] >= 1
    summary = client.get("/api/admin/content-audit/summary", headers=auth_headers).json()
    assert summary["lexicon_updated_at"]


def test_lexicon_sync_failure_returns_502(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    fake = FakeLexiconClient(error=httpx.ConnectError("offline"))
    monkeypatch.setattr(content_audit.httpx, "Client", lambda **_kwargs: fake)
    response = client.post("/api/admin/content-audit/lexicon/sync", headers=auth_headers)
    assert response.status_code == 502
    assert "词库同步失败" in response.json()["detail"]


def test_lexicon_downloads_when_cache_empty(client: TestClient, monkeypatch) -> None:
    fake = FakeLexiconClient()
    monkeypatch.setattr(content_audit.httpx, "Client", lambda **_kwargs: fake)
    state = content_audit.load_lexicon(force=True)
    assert state.ok is True
    assert fake.calls >= 1
    assert "暴恐词库" in state.categories
    cache = content_audit.lexicon_dir()
    assert (cache / "暴恐词库.txt").exists()
    fake.calls = 0
    content_audit.reset_content_audit_cache()
    again = content_audit.load_lexicon(force=True)
    assert again.ok is True
    assert fake.calls == 0


def test_lexicon_download_failure_degrades(client: TestClient, monkeypatch) -> None:
    fake = FakeLexiconClient(error=httpx.ConnectError("offline"))
    monkeypatch.setattr(content_audit.httpx, "Client", lambda **_kwargs: fake)
    state = content_audit.load_lexicon(force=True)
    assert state.ok is False
    assert "词库未能加载" in (state.error_message or "")


def test_lexicon_failure_still_records_pii(client: TestClient, tmp_path: Path) -> None:
    make_log(messages=[{"role": "user", "content": f"卡号 {VALID_CARD} 测试敏感词"}])
    result = content_audit.run_scan_batch_sync(batch_size=50, lexicon_directory=tmp_path / "nope")
    assert result["lexicon_ok"] is False
    assert result["new_findings"] >= 1
    session = get_session_factory()()
    try:
        categories = {row.category for row in session.scalars(select(ContentAuditFinding)).all()}
        assert "pii" in categories
        assert "sensitive" not in categories
    finally:
        session.close()


def test_scan_start_pause_resume_stop(client: TestClient, auth_headers: dict[str, str], tmp_path: Path, monkeypatch) -> None:
    lexicon = write_lexicon(tmp_path)
    monkeypatch.setattr(content_audit, "lexicon_dir", lambda: lexicon)
    monkeypatch.setattr(content_audit, "SCAN_PAUSE_SECONDS", 0.01)
    make_log(messages=[{"role": "user", "content": f"卡号 {VALID_CARD}"}])
    assert client.post("/api/admin/content-audit/scan/start").status_code == 401
    started = client.post("/api/admin/content-audit/scan/start", headers=auth_headers)
    assert started.status_code == 200
    assert started.json()["started"] is True
    paused = client.post("/api/admin/content-audit/scan/pause", headers=auth_headers)
    assert paused.status_code == 200
    summary = client.get("/api/admin/content-audit/summary", headers=auth_headers).json()
    assert summary["paused"] is True or summary["running"] is False
    resumed = client.post("/api/admin/content-audit/scan/resume", headers=auth_headers)
    assert resumed.status_code == 200
    stopped = client.post("/api/admin/content-audit/scan/stop", headers=auth_headers)
    assert stopped.status_code == 200
    content_audit.stop_scan()


def test_content_audit_job_default_and_busy(client: TestClient, auth_headers: dict[str, str]) -> None:
    listed = client.get("/api/admin/jobs", headers=auth_headers)
    audit = next(item for item in listed.json()["items"] if item["id"] == "content_audit")
    assert audit["kind"] == "manual"
    assert audit["params"] == []
    class Busy:
        def locked(self) -> bool:
            return True

    from app.services import jobs as jobs_service

    jobs_service._locks[JOB_CONTENT_AUDIT] = Busy()
    response = client.post("/api/admin/jobs/content_audit/run", headers=auth_headers)
    assert response.status_code == 409
    assert "任务正在运行" in response.json()["detail"]


def test_findings_api_auth_filter_page_and_mask(client: TestClient, auth_headers: dict[str, str], tmp_path: Path) -> None:
    assert client.get("/api/admin/content-audit/findings").status_code == 401
    lexicon = write_lexicon(tmp_path)
    make_log(messages=[{"role": "user", "content": f"卡号 {VALID_CARD} 密钥 {SK} 爆炸装置"}])
    content_audit.run_scan_batch_sync(batch_size=50, lexicon_directory=lexicon)
    too_big = client.get(
        "/api/admin/content-audit/findings",
        headers=auth_headers,
        params={"page_size": 101},
    )
    assert too_big.status_code == 422
    secrets = client.get(
        "/api/admin/content-audit/findings",
        headers=auth_headers,
        params={"category": "secret", "page_size": 20},
    )
    assert secrets.status_code == 200
    body = secrets.json()
    assert body["page_size"] == 20
    assert body["items"]
    excerpt = body["items"][0]["excerpt"]
    assert SK not in excerpt
    pii = client.get(
        "/api/admin/content-audit/findings",
        headers=auth_headers,
        params={"category": "pii", "severity": "high"},
    ).json()
    assert all(item["category"] == "pii" and item["severity"] == "high" for item in pii["items"])
    assert VALID_CARD not in pii["items"][0]["excerpt"]
    sensitive = client.get(
        "/api/admin/content-audit/findings",
        headers=auth_headers,
        params={"category": "sensitive", "lexicon_category": "暴恐词库"},
    ).json()
    assert sensitive["items"]
    assert all(item["lexicon_category"] == "暴恐词库" for item in sensitive["items"])
    summary = client.get("/api/admin/content-audit/summary", headers=auth_headers)
    assert summary.status_code == 200
    payload = summary.json()
    assert payload["finding_count"] >= 1
    assert payload["total_logs"] >= 1
    assert "pii" in payload["by_category"]

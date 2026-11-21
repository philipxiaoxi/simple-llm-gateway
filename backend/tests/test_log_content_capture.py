import sqlite3

from app.services import log_content


def test_oversized_history_keeps_recent_prompt_and_reply(monkeypatch):
    monkeypatch.setattr(log_content, "_capture_counts", {})
    monkeypatch.setattr(log_content, "_queue", log_content.Queue(maxsize=1))
    request = {"messages": [{"role": "user", "content": "x" * 300_000},
                            {"role": "user", "content": "new question"}]}
    response = {"choices": [{"message": {"role": "assistant", "content": "reply"}}]}
    log_content.enqueue_content(1, "openai_chat", request, response, None, None)
    item = log_content._queue.get_nowait()
    assert item[2]["messages"] == request["messages"][-1:]
    assert item[3] == response
    assert log_content.capture_diagnostics()["capture_diagnostics"] == {"partial": 1}


def test_busy_archive_is_retried(monkeypatch, tmp_path):
    path = tmp_path / "archive.db"
    with sqlite3.connect(path, timeout=0) as blocker, sqlite3.connect(path, timeout=0) as writer:
        blocker.execute("CREATE TABLE content (value INTEGER)")
        blocker.execute("BEGIN IMMEDIATE")

        def write():
            writer.execute("INSERT INTO content VALUES (42)")
            writer.commit()

        monkeypatch.setattr(log_content, "_write_content", write)
        monkeypatch.setattr(log_content.time, "sleep", lambda _: blocker.rollback())
        log_content._write_with_retry(())
        assert writer.execute("SELECT value FROM content").fetchall() == [(42,)]

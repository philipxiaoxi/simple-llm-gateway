from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.generation.service import list_tasks
from app.models import GenerationAsset, GenerationTask


def test_list_tasks_batches_assets_and_counts_within_scope():
    engine = create_engine("sqlite://")
    GenerationTask.__table__.create(engine)
    GenerationAsset.__table__.create(engine)

    with Session(engine) as db:
        db.add_all([
            GenerationTask(id="a", kind="image", status="succeeded", mcp_key_id=1),
            GenerationTask(id="b", kind="image", status="succeeded", mcp_key_id=1),
            GenerationTask(id="c", kind="image", status="failed", mcp_key_id=1),
            GenerationTask(id="d", kind="video", status="failed", mcp_key_id=1),
            GenerationTask(id="e", kind="image", status="failed", mcp_key_id=2),
        ])
        db.add_all([
            GenerationAsset(id="a-second", task_id="a", role="result", index_no=1),
            GenerationAsset(id="a-first", task_id="a", role="result", index_no=0),
            GenerationAsset(id="b-input", task_id="b", role="input"),
        ])
        db.commit()

        statements = []

        @event.listens_for(engine, "before_cursor_execute")
        def record_statement(_conn, _cursor, statement, _params, _context, _many):
            statements.append(statement)

        data = list_tasks(db, status="succeeded", kinds={"image"}, mcp_key_id=1)
        assert len(statements) == 3
        assert data["total"] == 2
        assert data["counts"] == {"succeeded": 2, "failed": 1}
        items = {item["id"]: item for item in data["items"]}
        assert set(items) == {"a", "b"}
        assert [asset["id"] for asset in items["a"]["results"]] == ["a-first", "a-second"]
        assert [asset["id"] for asset in items["b"]["inputs"]] == ["b-input"]

        page = list_tasks(db, kind="image", mcp_key_id=1, page=2, page_size=2)
        assert page["total"] == 3
        assert len(page["items"]) == 1
        empty = list_tasks(db, kinds=set(), mcp_key_id=1)
        assert empty["items"] == []
        assert empty["counts"] == {}

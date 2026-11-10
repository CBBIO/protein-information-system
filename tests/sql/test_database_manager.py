"""Unit tests for DatabaseManager engine options (no database needed)."""
import protein_information_system.sql.base.database_manager as dbm


def test_engine_uses_utf8_client_encoding(monkeypatch):
    captured = {}

    def fake_create_engine(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(dbm, "create_engine", fake_create_engine)
    monkeypatch.setattr(dbm, "ensure_pgvector_extension", lambda engine: None)
    monkeypatch.setattr(dbm.Base.metadata, "create_all", lambda engine: None)

    dbm.DatabaseManager({
        "DB_USERNAME": "u", "DB_PASSWORD": "p", "DB_HOST": "h", "DB_PORT": 5432, "DB_NAME": "d",
    })

    assert captured["url"].startswith("postgresql+psycopg2://")
    assert captured["kwargs"]["connect_args"]["client_encoding"] == "utf8"

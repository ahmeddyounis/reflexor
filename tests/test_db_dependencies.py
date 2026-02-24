def test_db_dependencies_are_importable() -> None:
    __import__("sqlalchemy")
    __import__("aiosqlite")
    __import__("alembic")

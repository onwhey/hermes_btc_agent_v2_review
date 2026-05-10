from __future__ import annotations

import pytest

from app.core.config import AppSettings, load_settings
from app.core.exceptions import DatabaseError, RedisError
from app.storage.mysql import database as mysql_database
from app.storage.mysql import health as mysql_health
from app.storage.mysql import session as mysql_session
from app.storage.redis import client as redis_client
from app.storage.redis import health as redis_health
from scripts.check_infra import collect_infra_errors


def test_mysql_and_redis_settings_are_loaded_and_typed() -> None:
    settings = load_settings(
        env_file=None,
        environ={
            "MYSQL_HOST": "127.0.0.1",
            "MYSQL_PORT": "3307",
            "MYSQL_DATABASE": "hermes_btc_agent",
            "MYSQL_USER": "local_user",
            "MYSQL_PASSWORD": "local-mysql-secret",
            "MYSQL_CHARSET": "utf8mb4",
            "MYSQL_POOL_SIZE": "7",
            "MYSQL_MAX_OVERFLOW": "12",
            "MYSQL_POOL_RECYCLE": "1800",
            "MYSQL_POOL_PRE_PING": "true",
            "REDIS_HOST": "127.0.0.1",
            "REDIS_PORT": "6380",
            "REDIS_PASSWORD": "local-redis-secret",
            "REDIS_DB": "2",
            "REDIS_SOCKET_TIMEOUT": "3.5",
            "REDIS_DECODE_RESPONSES": "false",
        },
    )

    assert settings.mysql_port == 3307
    assert settings.mysql_pool_size == 7
    assert settings.mysql_max_overflow == 12
    assert settings.mysql_pool_recycle == 1800
    assert settings.mysql_pool_pre_ping is True
    assert settings.redis_port == 6380
    assert settings.redis_db == 2
    assert settings.redis_socket_timeout == 3.5
    assert settings.redis_decode_responses is False


def test_connection_summaries_redact_passwords() -> None:
    settings = AppSettings(
        mysql_host="127.0.0.1",
        mysql_database="hermes_btc_agent",
        mysql_user="local_user",
        mysql_password="local-mysql-secret",
        redis_host="127.0.0.1",
        redis_password="local-redis-secret",
    )

    mysql_summary = mysql_database.render_redacted_mysql_connection_info(settings)
    redis_summary = redis_client.render_redacted_redis_connection_info(settings)

    assert "local-mysql-secret" not in mysql_summary
    assert "local-redis-secret" not in redis_summary
    assert "***REDACTED***" in mysql_summary
    assert "***REDACTED***" in redis_summary


def test_mysql_connection_url_requires_safe_complete_settings() -> None:
    missing_settings = AppSettings(mysql_host="127.0.0.1")
    remote_test_settings = AppSettings(
        app_env="test",
        mysql_host="db.example.invalid",
        mysql_database="hermes_btc_agent",
        mysql_user="local_user",
    )

    with pytest.raises(DatabaseError):
        mysql_database.build_mysql_connection_url(missing_settings)

    with pytest.raises(DatabaseError):
        mysql_database.build_mysql_connection_url(remote_test_settings)


def test_create_mysql_engine_uses_config_without_connecting(monkeypatch) -> None:
    settings = AppSettings(
        mysql_host="127.0.0.1",
        mysql_database="hermes_btc_agent",
        mysql_user="local_user",
        mysql_password="local-mysql-secret",
        mysql_pool_size=9,
        mysql_max_overflow=11,
        mysql_pool_recycle=1200,
        mysql_pool_pre_ping=True,
    )
    captured: dict[str, object] = {}
    fake_engine = object()

    def fake_create_engine(url: str, **kwargs: object) -> object:
        captured["url"] = url
        captured["kwargs"] = kwargs
        return fake_engine

    monkeypatch.setattr(mysql_database, "_load_sqlalchemy_create_engine", lambda: fake_create_engine)

    engine = mysql_database.create_mysql_engine(settings)

    assert engine is fake_engine
    assert captured["kwargs"] == {
        "pool_size": 9,
        "max_overflow": 11,
        "pool_recycle": 1200,
        "pool_pre_ping": True,
        "future": True,
    }


def test_session_factory_and_session_scope_are_mockable(monkeypatch) -> None:
    fake_engine = object()
    captured: dict[str, object] = {}

    def fake_sessionmaker(**kwargs: object):
        captured.update(kwargs)
        return lambda: FakeSession()

    class FakeSession:
        def __init__(self) -> None:
            self.committed = False
            self.rolled_back = False
            self.closed = False

        def commit(self) -> None:
            self.committed = True

        def rollback(self) -> None:
            self.rolled_back = True

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(mysql_session, "_load_sessionmaker", lambda: fake_sessionmaker)
    factory = mysql_session.create_session_factory(engine=fake_engine)

    assert captured["bind"] is fake_engine
    assert captured["autoflush"] is False
    assert captured["autocommit"] is False
    assert captured["future"] is True
    assert isinstance(factory(), FakeSession)

    yielded_sessions: list[FakeSession] = []
    monkeypatch.setattr(mysql_session, "get_db_session", lambda **_: FakeSession())

    with mysql_session.session_scope(commit_on_success=True) as active_session:
        yielded_sessions.append(active_session)

    assert yielded_sessions[0].committed is True
    assert yielded_sessions[0].closed is True

    with pytest.raises(ValueError):
        with mysql_session.session_scope() as active_session:
            yielded_sessions.append(active_session)
            raise ValueError("boom")

    assert yielded_sessions[-1].rolled_back is True
    assert yielded_sessions[-1].closed is True


def test_create_redis_client_uses_config_without_ping(monkeypatch) -> None:
    settings = AppSettings(
        redis_host="127.0.0.1",
        redis_port=6380,
        redis_password="local-redis-secret",
        redis_db=3,
        redis_socket_timeout=2.5,
        redis_decode_responses=False,
    )
    captured: dict[str, object] = {}

    class FakeRedis:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(redis_client, "_load_redis_client_class", lambda: FakeRedis)

    client = redis_client.create_redis_client(settings)

    assert isinstance(client, FakeRedis)
    assert captured == {
        "host": "127.0.0.1",
        "port": 6380,
        "db": 3,
        "password": "local-redis-secret",
        "socket_timeout": 2.5,
        "decode_responses": False,
    }


def test_redis_client_rejects_remote_test_target() -> None:
    settings = AppSettings(app_env="test", redis_host="redis.example.invalid")

    with pytest.raises(RedisError):
        redis_client.create_redis_client(settings)


def test_health_failures_return_clear_sanitized_results(monkeypatch) -> None:
    mysql_settings = AppSettings(
        mysql_host="127.0.0.1",
        mysql_database="hermes_btc_agent",
        mysql_user="local_user",
        mysql_password="mysql-secret",
    )
    redis_settings = AppSettings(redis_host="127.0.0.1", redis_password="redis-secret")

    def fail_mysql_engine(_: AppSettings) -> object:
        raise RuntimeError("driver failed password=mysql-secret")

    def fail_redis_client(_: AppSettings) -> object:
        raise RuntimeError("redis failed password=redis-secret")

    monkeypatch.setattr(mysql_health, "create_mysql_engine", fail_mysql_engine)
    monkeypatch.setattr(redis_health, "create_redis_client", fail_redis_client)

    mysql_result = mysql_health.check_mysql_health(mysql_settings)
    redis_result = redis_health.check_redis_health(redis_settings)

    assert mysql_result.ok is False
    assert redis_result.ok is False
    assert "mysql-secret" not in mysql_result.message
    assert "redis-secret" not in redis_result.message
    assert "***REDACTED***" in mysql_result.message
    assert "***REDACTED***" in redis_result.message


def test_check_infra_can_skip_real_connections() -> None:
    settings = AppSettings()

    assert collect_infra_errors(
        settings=settings,
        check_mysql=False,
        check_redis=False,
    ) == []


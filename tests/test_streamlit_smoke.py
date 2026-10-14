from __future__ import annotations

import importlib

import pytest


def test_app_import_does_not_start_streamlit() -> None:
    app = importlib.import_module("app")
    assert callable(app.main)


def test_streamlit_dependency_importable_when_installed() -> None:
    streamlit = pytest.importorskip("streamlit")
    assert hasattr(streamlit, "set_page_config")


def test_app_starts_with_bundled_demo_dashboard() -> None:
    app_testing = pytest.importorskip("streamlit.testing.v1")
    app_test = app_testing.AppTest.from_file("app.py")
    app_test.run(timeout=20)

    assert not app_test.exception
    # app_test.tabs also lists tabs nested inside pages, so check the top-level
    # three appear in order rather than comparing the whole list.
    labels = [tab.label for tab in app_test.tabs]
    expected = ["Executive Command Center", "Tableau Board View", "Cluster Detection Lab"]
    assert [label for label in labels if label in expected] == expected
    assert any("NorthStar demo portfolio" in item.value for item in app_test.caption)


def test_empty_default_demo_database_reseeds_from_json(tmp_path) -> None:
    app = importlib.import_module("app")
    database = importlib.import_module("src.database")

    db_path = tmp_path / "northstar_demo.sqlite"
    database.initialize_empty_database(db_path)

    row_count, label = app.ensure_recruiter_demo_data(db_path, 0)

    assert row_count == 475
    assert label == app.DEMO_DATA_SOURCE_LABEL


def test_databricks_backend_without_settings_shows_an_error(monkeypatch) -> None:
    app_testing = pytest.importorskip("streamlit.testing.v1")
    monkeypatch.setenv("DRIFT_BACKEND", "databricks")
    for name in ("DATABRICKS_SERVER_HOSTNAME", "DATABRICKS_HTTP_PATH", "DATABRICKS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    app_test = app_testing.AppTest.from_file("app.py")
    app_test.run(timeout=20)

    assert not app_test.exception
    assert any("DATABRICKS_SERVER_HOSTNAME" in item.value for item in app_test.error)

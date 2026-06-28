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
    assert [tab.label for tab in app_test.tabs] == ["Executive Dashboard", "Agent & Cluster Workspace"]
    assert any("NorthStar demo portfolio" in item.value for item in app_test.caption)

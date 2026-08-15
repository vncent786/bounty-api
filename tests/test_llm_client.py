import sys
from types import ModuleType

from social_scraper.llm_client import (
    _move_import_path_to_front,
    _temporarily_unshadow_modules,
)


def test_temporarily_unshadow_modules_restores_host_module(monkeypatch):
    host_utils = ModuleType("utils")
    host_utils.origin = "bounty"
    imported_utils = ModuleType("utils")
    imported_utils.origin = "hermes"
    monkeypatch.setitem(sys.modules, "utils", host_utils)

    with _temporarily_unshadow_modules("utils"):
        assert "utils" not in sys.modules
        sys.modules["utils"] = imported_utils
        assert sys.modules["utils"] is imported_utils

    assert sys.modules["utils"] is host_utils


def test_move_import_path_to_front_reorders_existing_path(tmp_path, monkeypatch):
    embedded = tmp_path / "embedded"
    embedded.mkdir()
    other = str(tmp_path / "other")
    monkeypatch.setattr(sys, "path", [other, str(embedded), ""])

    _move_import_path_to_front(str(embedded))

    assert sys.path[0] == str(embedded)
    assert sys.path.count(str(embedded)) == 1
    assert sys.path[1:] == [other, ""]

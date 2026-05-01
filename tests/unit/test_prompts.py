"""Layer 7b — PromptManager unit tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.prompts import (
    MissingPromptVariable,
    PromptManager,
    PromptNotFound,
)


@pytest.fixture
def tmp_prompts(tmp_path: Path) -> Path:
    """Build a small registry on disk."""
    (tmp_path / "greet.v1.yaml").write_text(
        "name: greet\nversion: 1\ndescription: hello v1\n"
        "inputs: [name]\ntemplate: |\n  Hello, {name}!\n",
        encoding="utf-8",
    )
    (tmp_path / "greet.v2.yaml").write_text(
        "name: greet\nversion: 2\ndescription: hello v2\n"
        "inputs: [name]\ntemplate: |\n  Hi {name}, welcome!\n",
        encoding="utf-8",
    )
    (tmp_path / "noinputs.v1.yaml").write_text(
        "name: noinputs\nversion: 1\ndescription: no vars\n"
        "inputs: []\ntemplate: |\n  static text only\n",
        encoding="utf-8",
    )
    return tmp_path


class TestPromptManager:
    def test_loads_all_yaml_files(self, tmp_prompts: Path):
        pm = PromptManager(root=tmp_prompts)
        listing = pm.list_prompts()
        assert listing == {"greet": [1, 2], "noinputs": [1]}

    def test_get_latest_returns_highest_version(self, tmp_prompts: Path):
        pm = PromptManager(root=tmp_prompts)
        latest = pm.get("greet", "latest")
        assert latest.version == 2

    def test_get_specific_version(self, tmp_prompts: Path):
        pm = PromptManager(root=tmp_prompts)
        v1 = pm.get("greet", 1)
        assert v1.version == 1
        assert "Hello" in v1.template

    def test_render_substitutes_vars(self, tmp_prompts: Path):
        pm = PromptManager(root=tmp_prompts)
        out = pm.render("greet", version=1, name="alice")
        assert "Hello, alice!" in out.text
        assert out.name == "greet"
        assert out.version == 1

    def test_missing_variable_raises(self, tmp_prompts: Path):
        pm = PromptManager(root=tmp_prompts)
        with pytest.raises(MissingPromptVariable):
            pm.render("greet", version=1)  # name missing

    def test_unknown_name_raises(self, tmp_prompts: Path):
        pm = PromptManager(root=tmp_prompts)
        with pytest.raises(PromptNotFound):
            pm.get("nope")

    def test_unknown_version_raises(self, tmp_prompts: Path):
        pm = PromptManager(root=tmp_prompts)
        with pytest.raises(PromptNotFound):
            pm.get("greet", 99)

    def test_default_root_loads_real_prompts(self):
        """The repo's real `app/prompts/` should load without errors."""
        PromptManager.reset_instance()
        pm = PromptManager.get_instance()
        listing = pm.list_prompts()
        # at least the three Layer 7 prompts must exist
        assert "chat_rag" in listing
        assert "chat_chitchat" in listing
        assert "query_understanding" in listing

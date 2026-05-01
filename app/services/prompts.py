"""Prompt registry — load YAML templates from `app/prompts/*.yaml` once at
process start. Each template carries `name + version` metadata; rendering
returns the text plus that metadata for downstream logging / evaluation
report attribution.

YAML schema:
    name: chat_rag
    version: 1
    description: Main RAG answer prompt with citation enforcement
    inputs: [context, history, question]
    template: |
        ...
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import yaml

from app.core.logger import get_logger

log = get_logger(__name__)

DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "prompts"


class PromptError(Exception):
    """Base for prompt-related errors."""


class PromptNotFound(PromptError):
    pass


class MissingPromptVariable(PromptError):
    pass


@dataclass(frozen=True)
class RenderedPrompt:
    text: str
    name: str
    version: int


@dataclass
class PromptTemplate:
    name: str
    version: int
    description: str
    inputs: list[str]
    template: str

    def render(self, **vars) -> RenderedPrompt:
        missing = [k for k in self.inputs if k not in vars]
        if missing:
            raise MissingPromptVariable(
                f"prompt {self.name}.v{self.version} missing variables: {missing}"
            )
        try:
            text = self.template.format(**vars)
        except KeyError as e:
            raise MissingPromptVariable(
                f"prompt {self.name}.v{self.version} references undefined {{{e}}}"
            ) from e
        return RenderedPrompt(text=text, name=self.name, version=self.version)


class PromptManager:
    """Process-wide singleton holding the prompt registry."""

    _instance: ClassVar["PromptManager | None"] = None
    _lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root else DEFAULT_ROOT
        self._registry: dict[str, dict[int, PromptTemplate]] = {}
        self._load_all()

    # ----------------------------------------------------------- loading

    def _load_all(self) -> None:
        if not self.root.exists():
            log.warning("prompts.root_missing", path=str(self.root))
            return
        for yaml_path in sorted(self.root.glob("*.yaml")):
            try:
                self._load_one(yaml_path)
            except Exception as e:  # noqa: BLE001
                log.error("prompts.load_failed", path=str(yaml_path), err=str(e))
                raise
        names = {n: sorted(v.keys()) for n, v in self._registry.items()}
        log.info("prompts.loaded", count=sum(len(v) for v in self._registry.values()),
                 templates=names)

    def _load_one(self, path: Path) -> None:
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        if not isinstance(raw, dict):
            raise PromptError(f"{path}: expected mapping at root")

        for required in ("name", "version", "template"):
            if required not in raw:
                raise PromptError(f"{path}: missing field '{required}'")

        tpl = PromptTemplate(
            name=str(raw["name"]),
            version=int(raw["version"]),
            description=str(raw.get("description", "")),
            inputs=list(raw.get("inputs", [])),
            template=str(raw["template"]),
        )
        self._registry.setdefault(tpl.name, {})[tpl.version] = tpl

    # ----------------------------------------------------------- public

    def get(self, prompt_name: str, version: int | str = "latest") -> PromptTemplate:
        versions = self._registry.get(prompt_name)
        if not versions:
            raise PromptNotFound(f"no prompt named {prompt_name!r}")
        if version == "latest":
            return versions[max(versions)]
        if int(version) not in versions:
            raise PromptNotFound(
                f"prompt {prompt_name!r} has no version {version}; "
                f"available: {sorted(versions)}"
            )
        return versions[int(version)]

    def render(
        self, prompt_name: str, *, version: int | str = "latest", **vars
    ) -> RenderedPrompt:
        """Render template `prompt_name` (latest or specified version) with vars.

        First arg is `prompt_name` (not `name`) so a template input variable
        called `name` doesn't collide with the parameter slot.
        """
        return self.get(prompt_name, version).render(**vars)

    def list_prompts(self) -> dict[str, list[int]]:
        return {n: sorted(v.keys()) for n, v in self._registry.items()}

    # ----------------------------------------------------------- singleton

    @classmethod
    def get_instance(cls) -> "PromptManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            cls._instance = None

"""Schema for Query Understanding output (used as Gemini structured response)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class UnderstandingResult(BaseModel):
    """One-shot output covering coref + intent + (optional) multi-query."""

    intent: Literal["needs_retrieval", "chitchat"] = Field(
        description='"needs_retrieval" if the user needs knowledge-base lookup; '
                    '"chitchat" for greetings/thanks/small talk.',
    )
    resolved_query: str = Field(
        description="Original query with pronouns/references resolved using history. "
                    "If no resolution needed or no history, return original query.",
    )
    rewrites: list[str] = Field(
        default_factory=list,
        description="2-3 paraphrased queries (only when enable_multi_query is true "
                    "and intent is needs_retrieval); empty list otherwise.",
    )

    @property
    def is_chitchat(self) -> bool:
        return self.intent == "chitchat"

    def all_queries(self) -> list[str]:
        """`resolved_query` + non-duplicate rewrites."""
        out = [self.resolved_query]
        for r in self.rewrites:
            if r and r not in out:
                out.append(r)
        return out

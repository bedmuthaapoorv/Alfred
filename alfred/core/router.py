"""Model router: code asks for a *tier*, config decides the model ID.

Tiers:
  triage  - high volume, cheap: classify/summarize incoming events
  draft   - routine writing: reply drafts, briefs
  plan    - deciding what to do, multi-step tool use
  reflect - nightly self-review and self-modification proposals
"""

from __future__ import annotations

from enum import Enum

from alfred.config import ModelsConfig


class Tier(str, Enum):
    TRIAGE = "triage"
    DRAFT = "draft"
    PLAN = "plan"
    REFLECT = "reflect"


class ModelRouter:
    def __init__(self, models: ModelsConfig) -> None:
        self._models = models

    def model_for(self, tier: Tier | str) -> str:
        tier = Tier(tier)
        model = getattr(self._models, tier.value)
        if not model:
            raise ValueError(f"no model configured for tier {tier.value!r}")
        return model

    def table(self) -> dict[str, str]:
        return {t.value: self.model_for(t) for t in Tier}

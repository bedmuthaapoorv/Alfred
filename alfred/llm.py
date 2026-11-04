"""Claude access: one place for model calls, cost accounting and the budget.

Every call is recorded in `llm_calls` with its token usage and dollar cost.
Before each call the day's spend is checked against `budget.max_daily_usd`.
Over budget, calls raise `BudgetExceeded` and callers fall back to rules.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

import anthropic

from alfred.core.router import ModelRouter, Tier
from alfred.core.store import Store

log = logging.getLogger(__name__)

# USD per million tokens: (input, output). Cache reads bill at 0.1x input and
# 5-minute cache writes at 1.25x input. Haiku 5.5's rate is for prompts up to
# 100K tokens; triage prompts are far below that.
PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-fable-5-1": (10.00, 50.00),
}
_UNKNOWN_MODEL_PRICE = (10.00, 50.00)  # unknown model: assume the dearest so the budget errs safe

# Thinking depth per tier. Triage is high-volume classification: keep it light.
EFFORT: dict[Tier, str] = {
    Tier.TRIAGE: "low",
    Tier.DRAFT: "medium",
    Tier.PLAN: "high",
    Tier.REFLECT: "high",
}


class LLMError(RuntimeError):
    pass


class BudgetExceeded(LLMError):
    pass


class LLMRefused(LLMError):
    pass


@dataclass
class LLMResult:
    text: str
    data: Any  # parsed JSON when a schema was given, else None
    model: str
    usd: float


def cost_usd(model: str, usage: Any) -> float:
    price_in, price_out = PRICES.get(model, _UNKNOWN_MODEL_PRICE)
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return (
        usage.input_tokens * price_in
        + cache_read * price_in * 0.1
        + cache_write * price_in * 1.25
        + usage.output_tokens * price_out
    ) / 1_000_000


def start_of_day(tz: ZoneInfo, now: datetime | None = None) -> datetime:
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    return datetime.combine(now.date(), time(0), tzinfo=tz)


class LLM:
    def __init__(
        self,
        router: ModelRouter,
        store: Store,
        *,
        max_daily_usd: float,
        tz: ZoneInfo,
        client: Any | None = None,
    ) -> None:
        self.router = router
        self.store = store
        self.max_daily_usd = max_daily_usd
        self.tz = tz
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            # Credentials resolve from ANTHROPIC_API_KEY (or an `ant auth login` profile).
            self._client = anthropic.AsyncAnthropic(max_retries=3, timeout=120.0)
        return self._client

    def spent_today(self) -> float:
        return self.store.spend_since(start_of_day(self.tz))

    async def complete(
        self,
        *,
        tier: Tier,
        purpose: str,
        system: str,
        user: str,
        max_tokens: int,
        schema: dict[str, Any] | None = None,
    ) -> LLMResult:
        spent = self.spent_today()
        if spent >= self.max_daily_usd:
            raise BudgetExceeded(f"daily budget ${self.max_daily_usd:.2f} reached (spent ${spent:.2f})")

        model = self.router.model_for(tier)
        output_config: dict[str, Any] = {"effort": EFFORT[tier]}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}

        try:
            response = await self.client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config=output_config,
            )
        except anthropic.APIError as e:
            self.store.record_llm_call(
                tier=tier.value, model=model, purpose=purpose, input_tokens=0, output_tokens=0, usd=0.0,
                error=f"{type(e).__name__}: {e}"[:500],
            )
            raise LLMError(f"{type(e).__name__}: {e}") from e

        usd = cost_usd(model, response.usage)
        self.store.record_llm_call(
            tier=tier.value,
            model=model,
            purpose=purpose,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_read=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            cache_write=getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
            usd=usd,
            stop_reason=response.stop_reason,
            request_id=getattr(response, "_request_id", None),
        )

        if response.stop_reason == "refusal":
            raise LLMRefused(f"{purpose}: model declined")
        if response.stop_reason == "max_tokens":
            raise LLMError(f"{purpose}: output hit max_tokens={max_tokens}")

        text = "".join(b.text for b in response.content if b.type == "text").strip()
        data = None
        if schema is not None:
            try:
                data = json.loads(text)
            except json.JSONDecodeError as e:
                raise LLMError(f"{purpose}: invalid JSON from model") from e
        return LLMResult(text=text, data=data, model=model, usd=usd)


def untrusted(label: str, content: str) -> str:
    """Wrap external content so prompts can tell data from instructions."""
    content = content.replace("</untrusted_content>", "</untrusted_content_>")
    return f'<untrusted_content source="{label}">\n{content}\n</untrusted_content>'

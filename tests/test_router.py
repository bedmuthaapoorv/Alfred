import pytest

from alfred.config import ModelsConfig
from alfred.core.router import ModelRouter, Tier


def test_tier_lookup():
    r = ModelRouter(ModelsConfig(triage="t", draft="d", plan="p", reflect="r"))
    assert r.model_for(Tier.TRIAGE) == "t"
    assert r.model_for("plan") == "p"
    assert r.table() == {"triage": "t", "draft": "d", "plan": "p", "reflect": "r"}
    with pytest.raises(ValueError):
        r.model_for("nope")

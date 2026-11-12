"""Which provider and model the brain can run on (#112): a fixed allowlist.

A model is offered here only after it passed the brain eval with zero wrong presses
(``tools/bench/tycho_eval.py``; the bar is in #112). There are no free-text model ids:
the settings name a provider and one of its models from this list, and nothing else
can be picked. Pure data, so the settings schema can read it without the adapters.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

type Provider = Literal["anthropic", "openrouter"]
type BrainModel = Literal["Claude Haiku 5.5"]

ANTHROPIC_HAIKU = "claude-haiku-5-5"
OPENROUTER_HAIKU = "anthropic/claude-haiku-5.5"

OFFERED: Mapping[Provider, Mapping[BrainModel, str]] = {
    # 185/191 with 0 wrong presses, after #271, #276, #278, #280 and #281 (11 Nov 2026).
    "anthropic": {"Claude Haiku 5.5": ANTHROPIC_HAIKU},
    # The same model, forwarded by OpenRouter to Anthropic: it scored as direct did (#262).
    "openrouter": {"Claude Haiku 5.5": OPENROUTER_HAIKU},
}
"""Each provider's models, by the name players see, to the provider's own id."""

LABELS: Mapping[Provider, str] = {"anthropic": "Anthropic", "openrouter": "OpenRouter"}


@dataclass(frozen=True, slots=True)
class Choice:
    """The brain a player picked: the provider, the model's name and its id there."""

    provider: Provider
    model: BrainModel
    model_id: str

    @property
    def key(self) -> str:
        """The name its key is stored under (``tycho key set <key>``)."""
        return self.provider

    @property
    def label(self) -> str:
        return LABELS[self.provider]

    def describe(self) -> str:
        if self.provider == "anthropic":
            return f"{self.model}, direct from Anthropic"
        return f"{self.model} through {self.label}"


def choose(provider: Provider, model: BrainModel) -> Choice:
    """The choice for a provider and model. Raises ``ValueError`` if the provider
    doesn't offer that model."""
    models = OFFERED.get(provider, {})
    if model not in models:
        offered = ", ".join(models) or "nothing"
        raise ValueError(f"{LABELS.get(provider, provider)} offers {offered} as the brain")
    return Choice(provider, model, models[model])

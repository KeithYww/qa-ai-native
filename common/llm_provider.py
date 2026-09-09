# SPDX-FileCopyrightText: 2025-2026 Taras Paruta (partarstu@gmail.com)
#
# SPDX-License-Identifier: AGPL-3.0-only

"""Builds pydantic-ai models backed by the Photon API relay (Anthropic-compatible protocol)."""

from dataclasses import replace

from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.profiles.anthropic import anthropic_model_profile
from pydantic_ai.providers.anthropic import AnthropicProvider

import config

# Photon relay model aliases whose backend rejects the legacy budget-based thinking format and
# requires the newer adaptive format instead. pydantic_ai can't detect this on its own because
# these aliases don't match its canonical Anthropic model name prefixes. Other aliases (e.g.
# "gpt-5.6-luna") work fine with the legacy format, so the override below is scoped to only
# these confirmed names instead of being applied to every model.
_MODELS_REQUIRING_ADAPTIVE_THINKING = frozenset({"claude-sonnet-5", "gpt-5.6-terra"})


def _photon_model_profile(model_name: str):
    """Patches the auto-detected Anthropic profile for Photon relay model aliases that need it."""
    profile = anthropic_model_profile(model_name)
    if model_name in _MODELS_REQUIRING_ADAPTIVE_THINKING:
        return replace(profile, anthropic_supports_adaptive_thinking=True)
    return profile


def get_model(primary_model_name: str, fallback_model_name: str) -> Model:
    """Builds a Photon API model that falls back to a secondary model on API errors.

    The relay is a third-party proxy in front of multiple providers, so any given model can
    intermittently error out; `FallbackModel` retries the request against `fallback_model_name`
    when that happens.
    """
    provider = AnthropicProvider(api_key=config.PHOTON_API_KEY, base_url=config.PHOTON_API_BASE_URL)
    primary_model = AnthropicModel(
        primary_model_name, provider=provider, profile=_photon_model_profile(primary_model_name)
    )
    fallback_model = AnthropicModel(
        fallback_model_name, provider=provider, profile=_photon_model_profile(fallback_model_name)
    )
    return FallbackModel(primary_model, fallback_model)

"""
llm_client.py — Provider-agnostic LLM wrapper for Your Humble EquityBot.

Supports Claude (Anthropic) and GPT-4o (OpenAI) with identical interface.
Switch providers by changing LLM_PROVIDER in .env — no other code changes needed.

Adversarial mode: run both providers and cross-review each other's analysis.
"""

from __future__ import annotations
import json
import logging
import re
import time
from typing import Optional

from config import (
    ANTHROPIC_API_KEY, OPENAI_API_KEY,
    LLM_PROVIDER, LLM_MODEL, ADVERSARIAL_MODE,
)

logger = logging.getLogger(__name__)


class LLMClient:
    """
    Single interface to any configured LLM provider.

    Usage:
        client = LLMClient()
        text   = client.generate(user_prompt, system_prompt)
        parsed = client.generate_json(user_prompt, system_prompt)

    Prompt caching (Claude only):
        Pass cacheable_prefix=<fixed_text> to generate()/generate_json().
        The prefix is sent as a separate content block marked cache_control:ephemeral.
        Anthropic caches it for 5 minutes — 90% cost reduction on re-reads.
        Requires ≥ 1024 tokens in the prefix + system prompt combined.

    Token usage:
        After each Claude call, self.last_usage is populated:
        {input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens}
    """

    def __init__(self, provider: str = "", model: str = ""):
        self.provider   = provider or LLM_PROVIDER
        self.model      = model    or LLM_MODEL
        self.last_usage: dict = {}   # populated after each Claude call

        if not self._api_key():
            logger.warning(
                f"[LLMClient] No API key found for provider '{self.provider}'. "
                f"Add the key to your .env file."
            )

    # ── Public API ────────────────────────────────────────────────────────────

    def generate(
        self,
        user_prompt: str,
        system_prompt: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.3,
        cacheable_prefix: str = "",
    ) -> str:
        """
        Generate a text response from the configured LLM.
        Temperature 0.3 = creative but consistent (good for analyst reports).

        cacheable_prefix: fixed text sent before user_prompt as a separate content
        block with cache_control:ephemeral (Claude only). Use this for the framework
        instructions / output schema portion of the prompt — it stays the same across
        runs of the same framework, so Anthropic can cache and re-read it cheaply.
        """
        start = time.time()
        logger.info(f"[LLMClient] Calling {self.provider}/{self.model} "
                    f"(~{(len(cacheable_prefix)+len(user_prompt))//4} tokens in)…")

        if self.provider == "claude":
            result = self._claude(user_prompt, system_prompt, max_tokens, temperature,
                                  cacheable_prefix)
        elif self.provider == "openai":
            result = self._openai(user_prompt, system_prompt, max_tokens, temperature)
        else:
            raise ValueError(f"Unknown LLM provider: '{self.provider}'. "
                             f"Set LLM_PROVIDER=claude or LLM_PROVIDER=openai in .env")

        elapsed = time.time() - start
        u = self.last_usage
        cache_hit = u.get("cache_read_input_tokens", 0)
        cache_new = u.get("cache_creation_input_tokens", 0)
        logger.info(
            f"[LLMClient] Response: ~{len(result)//4} tokens out, {elapsed:.1f}s"
            + (f" | cache_hit={cache_hit} cache_write={cache_new}" if (cache_hit or cache_new) else "")
        )
        return result

    def generate_json(
        self,
        user_prompt: str,
        system_prompt: str = "",
        max_tokens: int = 4096,
        cacheable_prefix: str = "",
    ) -> dict:
        """
        Generate and parse a JSON response.
        Automatically handles markdown code blocks and minor formatting issues.
        Falls back to empty dict on parse failure with an error log.

        cacheable_prefix: see generate() — passed through unchanged.
        """
        # Ask explicitly for JSON output
        json_instruction = (
            "\n\nIMPORTANT: Return ONLY valid JSON. "
            "No markdown, no code blocks, no commentary before or after the JSON object."
        )
        raw = self.generate(user_prompt + json_instruction, system_prompt, max_tokens,
                            cacheable_prefix=cacheable_prefix)

        # Strip any markdown code fences the model might add despite instructions
        cleaned = _strip_code_fences(raw)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            # Fallback 1: find first { to last } (handles trailing commentary)
            start = cleaned.find('{')
            end   = cleaned.rfind('}')
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(cleaned[start:end+1])
                except json.JSONDecodeError:
                    pass
            # Fallback 2: regex extraction
            match = re.search(r'\{.*\}', cleaned, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass
            logger.error(
                f"[LLMClient] JSON parse failed. Raw response (first 500 chars):\n"
                f"{raw[:500]}"
            )
            return {}

    def check_configured(self) -> tuple[bool, str]:
        """
        Returns (is_ready, message) to show users in the UI.
        """
        key = self._api_key()
        if not key:
            provider_label = "ANTHROPIC_API_KEY" if self.provider == "claude" else "OPENAI_API_KEY"
            return False, (
                f"No API key found for '{self.provider}'. "
                f"Add {provider_label} to your .env file."
            )
        return True, f"Ready — {self.provider}/{self.model}"

    # ── Provider implementations ──────────────────────────────────────────────

    def _claude(
        self,
        user_prompt: str,
        system_prompt: str,
        max_tokens: int,
        temperature: float,
        cacheable_prefix: str = "",
    ) -> str:
        try:
            import anthropic
        except ImportError:
            raise ImportError("Run: pip install anthropic")

        key = ANTHROPIC_API_KEY
        if not key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set. Add it to .env:\n"
                "  ANTHROPIC_API_KEY=sk-ant-..."
            )

        client = anthropic.Anthropic(api_key=key)

        # ── Build user content (multi-block when caching) ─────────────────────
        if cacheable_prefix:
            # Split into: [fixed framework instructions (cached)] + [variable company data]
            user_content = [
                {
                    "type": "text",
                    "text": cacheable_prefix,
                    "cache_control": {"type": "ephemeral"},
                },
                {
                    "type": "text",
                    "text": user_prompt,
                },
            ]
        else:
            user_content = user_prompt

        # ── Build system content ──────────────────────────────────────────────
        # Mark the system prompt cacheable too when we're in caching mode —
        # the combined (system + cacheable_prefix) token count is what
        # Anthropic checks against the 1024-token minimum cache threshold.
        if system_prompt and cacheable_prefix:
            system_content = [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        elif system_prompt:
            system_content = system_prompt
        else:
            system_content = None

        kwargs: dict = dict(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": user_content}],
        )
        if system_content is not None:
            kwargs["system"] = system_content

        try:
            msg = client.messages.create(**kwargs)
            # ── Track token usage (includes cache stats) ──────────────────────
            u = msg.usage
            self.last_usage = {
                "input_tokens":               u.input_tokens,
                "output_tokens":              u.output_tokens,
                "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
                "cache_read_input_tokens":     getattr(u, "cache_read_input_tokens",     0) or 0,
            }
            return msg.content[0].text
        except anthropic.AuthenticationError:
            raise RuntimeError(
                "Invalid ANTHROPIC_API_KEY. Check your key at console.anthropic.com"
            )
        except anthropic.RateLimitError:
            raise RuntimeError(
                "Anthropic rate limit hit. Wait a moment and try again."
            )
        except Exception as e:
            raise RuntimeError(f"Claude API error: {e}")

    def _openai(
        self,
        user_prompt: str,
        system_prompt: str,
        max_tokens: int,
        temperature: float,
        cacheable_prefix: str = "",   # prepended to user_prompt (no server-side caching for OpenAI)
    ) -> str:
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("Run: pip install openai")

        key = OPENAI_API_KEY
        if not key:
            raise RuntimeError(
                "OPENAI_API_KEY not set. Add it to .env:\n"
                "  OPENAI_API_KEY=sk-..."
            )

        client = OpenAI(api_key=key)
        # Prepend cacheable_prefix to user_prompt — OpenAI has no server-side
        # prompt caching via content blocks, so we just concatenate both parts
        # into one message. The full schema + instructions + data all arrive together.
        if cacheable_prefix:
            user_prompt = cacheable_prefix + "\n\n" + user_prompt
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        try:
            resp = client.chat.completions.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=messages,
            )
            return resp.choices[0].message.content
        except Exception as e:
            raise RuntimeError(f"OpenAI API error: {e}")

    def _api_key(self) -> str:
        if self.provider == "claude":
            return ANTHROPIC_API_KEY
        elif self.provider == "openai":
            return OPENAI_API_KEY
        return ""


# ── Adversarial review client ─────────────────────────────────────────────────

class AdversarialReviewer:
    """
    Runs two LLM providers independently on the same analysis task,
    then has each critique the other's output. Returns a merged, higher-confidence report.

    Used when ADVERSARIAL_MODE=true in .env.
    """

    def __init__(self):
        self.primary   = LLMClient(provider="claude",  model="claude-sonnet-4-5")
        self.secondary = LLMClient(provider="openai",  model="gpt-4o")

    def generate_with_review(
        self, user_prompt: str, system_prompt: str, max_tokens: int = 4096
    ) -> dict:
        """
        Both models generate independently → each critiques the other →
        final synthesis highlights agreements and flags disagreements.

        Returns dict with keys: primary, secondary, critique_of_primary,
        critique_of_secondary, consensus_fields, contested_fields.
        """
        logger.info("[Adversarial] Running dual-model analysis…")

        # Step 1: Independent analysis (parallel would be faster, sequential for simplicity)
        primary_raw   = self.primary.generate_json(user_prompt, system_prompt, max_tokens)
        secondary_raw = self.secondary.generate_json(user_prompt, system_prompt, max_tokens)

        # Step 2: Cross-review
        critique_prompt = _build_critique_prompt(primary_raw, secondary_raw)
        critique_system = (
            "You are a senior risk analyst. Your job is to identify where two "
            "independent analyses disagree, flag overconfident claims, and note "
            "risks that one analysis missed. Be specific and cite evidence."
        )

        critique_of_primary   = self.secondary.generate(
            f"Review this analysis from Analyst A:\n{json.dumps(primary_raw, indent=2)}\n\n"
            f"Analyst B's analysis for comparison:\n{json.dumps(secondary_raw, indent=2)}\n\n"
            f"Critique Analyst A's analysis. What did they miss or overstate?",
            critique_system, 1024
        )
        critique_of_secondary = self.primary.generate(
            f"Review this analysis from Analyst B:\n{json.dumps(secondary_raw, indent=2)}\n\n"
            f"Analyst A's analysis for comparison:\n{json.dumps(primary_raw, indent=2)}\n\n"
            f"Critique Analyst B's analysis. What did they miss or overstate?",
            critique_system, 1024
        )

        # Step 3: Identify consensus vs. contested
        consensus, contested = _find_consensus_contested(primary_raw, secondary_raw)

        return {
            "primary":              primary_raw,
            "secondary":            secondary_raw,
            "critique_of_primary":  critique_of_primary,
            "critique_of_secondary": critique_of_secondary,
            "consensus_fields":     consensus,
            "contested_fields":     contested,
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _strip_code_fences(text: str) -> str:
    """
    Remove ```json ... ``` or ``` ... ``` wrappers, even with leading whitespace
    or multiple fence variations. Also handles nested backtick content.
    """
    text = text.strip()
    # Handle ```json or ``` at the start (possibly with whitespace)
    text = re.sub(r'^`{3,}(?:json|JSON)?\s*\n?', '', text, flags=re.MULTILINE)
    # Handle ``` at the end
    text = re.sub(r'\n?`{3,}\s*$', '', text)
    return text.strip()


def _build_critique_prompt(primary: dict, secondary: dict) -> str:
    return (
        f"Two independent analysts have produced these investment analyses.\n\n"
        f"Analyst A:\n{json.dumps(primary, indent=2)}\n\n"
        f"Analyst B:\n{json.dumps(secondary, indent=2)}\n\n"
        f"Identify: (1) Where they agree (high confidence), "
        f"(2) Where they disagree (contested — flag to investor), "
        f"(3) What risks or opportunities one raised that the other missed."
    )


def _find_consensus_contested(a: dict, b: dict) -> tuple[list, list]:
    """
    Compare recommendation fields between two analyses.
    Returns (consensus_fields, contested_fields).
    """
    consensus = []
    contested = []
    compare_keys = ["recommendation", "recommendation_rationale"]
    for key in compare_keys:
        va = a.get(key, "")
        vb = b.get(key, "")
        if isinstance(va, str) and isinstance(vb, str):
            if va.strip().lower() == vb.strip().lower():
                consensus.append(key)
            else:
                contested.append({"field": key, "primary": va[:200], "secondary": vb[:200]})
    return consensus, contested

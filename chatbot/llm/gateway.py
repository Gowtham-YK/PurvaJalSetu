"""
Juno AI LLM Gateway

This module is responsible for:
- Sending user messages to the LLM
- Understanding natural-language questions
- Extracting intent and entities
- Returning structured JSON
- Keeping LLM logic separate from Flask
"""

import json
import os

from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()


class JunoLLMGateway:
    """LLM gateway for Juno AI."""

    # Google exposes an OpenAI-compatible endpoint for Gemini models,
    # so we can keep using the `openai` client library and just point
    # it at Gemini instead of OpenAI's own API.
    # https://ai.google.dev/gemini-api/docs/openai
    GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

    def __init__(self):
        # "GEMINI_API_KE" (missing the trailing Y) is a legacy typo that
        # may still be set in some .env files - keep reading it as a
        # fallback so we don't break existing setups, but GEMINI_API_KEY
        # is the name to use going forward.
        api_key = (
            os.getenv("GEMINI_API_KEY")
            or os.getenv("GEMINI_API_KE")
        )

        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not configured in .env"
            )

        self.client = OpenAI(
            api_key=api_key,
            base_url=self.GEMINI_BASE_URL,
        )

        self.model = os.getenv(
            "JUNO_LLM_MODEL",
            "gemini-2.5-flash",
        )

    def understand(self, message: str) -> dict:
        """
        Convert a natural-language user message into
        structured intent and entities.
        """

        system_prompt = """
You are Juno, the AI assistant for JalSetu,
a wastewater and treated-water management platform.

Your job is to understand the user's request.

Return ONLY valid JSON.

Supported intents:

- greeting
- help
- user_role
- stp_information
- nearest_stp
- stp_recommendation
- stp_capacity_query
- order_status
- order_history
- latest_order
- order_quantity
- total_order_quantity
- order_count
- tanker_status
- delivery_status
- routing
- demand
- my_location
- general

Extract these entities when present:

- location
- quantity_kld
- order_id
- stp_name

Rules:

1. Understand natural language.
2. Understand spelling mistakes and informal language.
3. "for", "near", "in", "around", and "at"
   can introduce a location.
4. Never invent a location.
5. If a location is not explicitly mentioned,
   return null.
6. Never calculate STP distances yourself.
7. Never invent order status, STP capacity,
   delivery status, tanker status, or other
   live JalSetu data.
8. If the user asks about live JalSetu information,
   identify the correct intent and entities only.
9. Preserve the user's requested quantity in KLD
   when one is explicitly mentioned.

Return exactly this JSON structure:

{
  "intent": "...",
  "location": null,
  "quantity_kld": null,
  "order_id": null,
  "stp_name": null,
  "confidence": 0.0
}
"""

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message},
            ],
        )

        raw_output = (response.choices[0].message.content or "").strip()

        # Gemini sometimes wraps JSON in a ```json ... ``` fence even when
        # asked not to - strip that before parsing.
        if raw_output.startswith("```"):
            raw_output = raw_output.strip("`")
            if raw_output.lower().startswith("json"):
                raw_output = raw_output[4:]
            raw_output = raw_output.strip()

        try:
            result = json.loads(raw_output)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Juno LLM returned invalid JSON: {raw_output}"
            ) from exc

        return result
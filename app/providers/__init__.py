"""Provider factory — returns the right AI voice backend based on AI_PROVIDER in .env."""

from app.config import settings


def get_provider(call_id: str, system_prompt: str):
    """Return an OpenAI or Gemini provider instance based on AI_PROVIDER config."""
    provider = settings.ai_provider.lower()
    if provider == "gemini":
        from app.providers.gemini_provider import GeminiProvider
        return GeminiProvider(call_id, system_prompt)
    else:
        from app.providers.openai_provider import OpenAIProvider
        return OpenAIProvider(call_id, system_prompt)

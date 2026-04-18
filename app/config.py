import os
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

load_dotenv()


class Settings(BaseSettings):
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""
    openai_api_key: str = ""
    server_base_url: str = "http://localhost:8000"

    # ── AI Provider ──────────────────────────────────────────────────────────
    # Set AI_PROVIDER=gemini to use Gemini Live, or AI_PROVIDER=openai (default)
    ai_provider: str = "openai"

    # OpenAI Realtime config
    openai_realtime_model: str = "gpt-4o-mini-realtime-preview"
    openai_realtime_voice: str = "alloy"

    # Gemini Live config
    gemini_api_key: str = ""
    gemini_realtime_model: str = "models/gemini-2.0-flash-live-001"
    gemini_realtime_voice: str = "Aoede"  # Options: Aoede, Charon, Fenrir, Kore, Puck

    # ─────────────────────────────────────────────────────────────────────────

    # Call recording (Twilio dual-channel). Toggle via ENABLE_RECORDING in .env
    enable_recording: bool = True

    class Config:
        env_file = ".env"


settings = Settings()

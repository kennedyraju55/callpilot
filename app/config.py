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

    # OpenAI Realtime API config
    openai_realtime_model: str = "gpt-4o-realtime-preview"
    openai_realtime_voice: str = "alloy"

    # Client identity — the person the AI is calling on behalf of
    client_name: str = "Raju"

    class Config:
        env_file = ".env"


settings = Settings()

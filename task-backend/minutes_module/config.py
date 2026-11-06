import os
from pathlib import Path
from pydantic import BaseModel, Field
from dotenv import load_dotenv

class Settings(BaseModel):
    provider: str = ""
    model: str = ""
    temperature: float = Field(default=.1, ge=0, le=2)
    max_output_tokens: int = Field(default=4096, ge=128)
    output_token_ceiling: int = Field(default=16384, ge=128)
    length_retries: int = Field(default=2, ge=0, le=4)
    chunk_tokens: int = Field(default=2000, ge=500)
    requests_per_minute: int = Field(default=20, ge=1)
    tokens_per_minute: int = Field(default=8000, ge=1000)
    max_concurrency: int = Field(default=1, ge=1, le=8)
    cache_ttl_seconds: float = Field(default=900, ge=0)
    cache_entries: int = Field(default=128, ge=0)
    retry_jitter: float = Field(default=.25, ge=0, le=1)
    timeout_seconds: float = Field(default=90, gt=0)
    max_retries: int = Field(default=3, ge=0, le=8)
    enable_verification: bool = True
    storage_mode: str = Field(default="mongodb", pattern="^(auto|mongodb)$")
    context_tokens: int = Field(default=16000, ge=4000)
    max_input_chars: int = Field(default=1000000, ge=100)
    max_calls: int = Field(default=80, ge=1)
    total_timeout_seconds: float = Field(default=600, gt=0)
    pricing_file: Path | None = None
    log_level: str = "INFO"

    @classmethod
    def load(cls):
        # Exactly one backend dotenv file; shell variables still take precedence.
        env_file = Path(__file__).resolve().parents[1] / ".env"
        if env_file.is_file():
            load_dotenv(env_file, override=False)
        return cls(**{name: os.environ["MINUTES_"+name.upper()] for name in cls.model_fields if "MINUTES_"+name.upper() in os.environ})

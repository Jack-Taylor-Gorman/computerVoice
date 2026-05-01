"""Runtime configuration via env + .env."""
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 7862

    db_path: Path = ROOT / "storage" / "trekdata.sqlite"
    sources_dir: Path = ROOT / "storage" / "sources"
    cache_dir: Path = ROOT / "storage" / "cache"
    datasets_dir: Path = ROOT / "datasets"

    redis_url: str = "redis://127.0.0.1:6379/0"

    whisper_model: str = "large-v3"
    whisper_device: str = "cuda"
    whisper_compute_type: str = "float16"

    target_sample_rate: int = 24000
    target_lufs: float = -23.0
    min_clip_sec: float = 1.5
    max_clip_sec: float = 15.0
    auto_accept_snr_db: float = 28.0
    min_snr_db: float = 20.0


settings = Settings()

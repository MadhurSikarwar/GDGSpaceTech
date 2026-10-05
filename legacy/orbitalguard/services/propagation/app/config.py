import os
from pathlib import Path
from pydantic import Field
try:
    from pydantic_settings import BaseSettings
except ImportError:
    from pydantic import BaseSettings  # fallback for older pydantic versions


BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent.parent


class Settings(BaseSettings):
    APP_NAME: str = "OrbitalGuard Tracking & Screening Service"
    VERSION: str = "1.0.0"
    API_PREFIX: str = "/api/v1"
    
    # Database Settings (PostgreSQL default string with SQLite fallback)
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./orbitalguard.db")
    
    # Screening Settings
    SCREENING_THRESHOLD_KM: float = float(os.getenv("SCREENING_THRESHOLD_KM", "50.0"))
    COARSE_ALTITUDE_BUFFER_KM: float = float(os.getenv("COARSE_ALTITUDE_BUFFER_KM", "50.0"))

    # Probability-of-Collision (Pc) Settings
    COMBINED_HARD_BODY_RADIUS_M: float = float(os.getenv("COMBINED_HARD_BODY_RADIUS_M", "20.0"))
    PC_CRITICAL_THRESHOLD: float = float(os.getenv("PC_CRITICAL_THRESHOLD", "1e-4"))

    # Space Weather Settings (NOAA SWPC)
    SPACE_WEATHER_CACHE_TTL_SECONDS: float = float(os.getenv("SPACE_WEATHER_CACHE_TTL_SECONDS", "600.0"))
    SPACE_WEATHER_FETCH_TIMEOUT_SECONDS: float = float(os.getenv("SPACE_WEATHER_FETCH_TIMEOUT_SECONDS", "5.0"))
    # Quiet-sun fallback used only when NOAA is unreachable (flagged live=false).
    QUIET_SUN_KP: float = float(os.getenv("QUIET_SUN_KP", "2.0"))
    QUIET_SUN_AP: float = float(os.getenv("QUIET_SUN_AP", "7.0"))
    QUIET_SUN_F107_SFU: float = float(os.getenv("QUIET_SUN_F107_SFU", "150.0"))
    
    # Propagation Settings
    PROPAGATION_HORIZON_MINUTES: int = int(os.getenv("PROPAGATION_HORIZON_MINUTES", "90"))
    PROPAGATION_STEP_MINUTES: float = float(os.getenv("PROPAGATION_STEP_MINUTES", "1.0"))
    
    # CelesTrak Ingestion Settings
    CELESTRAK_BASE_URL: str = os.getenv("CELESTRAK_BASE_URL", "https://celestrak.org/NORAD/elements/gp.php")
    OFFLINE_MODE: bool = os.getenv("OFFLINE_MODE", "false").lower() in ("true", "1", "yes")
    
    # Paths
    DATA_DIR: Path = BASE_DIR / "data"
    CACHE_FILE: Path = BASE_DIR / "data" / "celestrak_cache.json"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()

import os

class Settings:
    PROJECT_NAME: str = "UZBEK LIFE RP"
    VERSION: str = "1.0.0"
    
    # Xavfsizlik
    SECRET_KEY: str = os.getenv("JWT_SECRET", "super-secret-key-change-in-production")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 kun
    
    # Ma'lumotlar bazasi
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./game.db")
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
    elif DATABASE_URL.startswith("postgresql://"):
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

settings = Settings()

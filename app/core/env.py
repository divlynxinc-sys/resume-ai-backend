import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


def load_env(app_env: Optional[str] = None) -> None:
    """
    Loads environment variables in this order (first existing wins, OS env always wins):
    1) .envs/.env.<APP_ENV>  (APP_ENV defaults to 'local')
    2) .env                   (project root)
    """
    app_env = app_env or os.getenv("APP_ENV", "local")

    # Respect already-set OS env; do not override by default
    env_candidates = [
        Path(".envs") / f".env.{app_env}",  # environment-specific
        Path(".env"),                       # fallback
    ]
    for path in env_candidates:
        if path.exists():
            load_dotenv(dotenv_path=str(path), override=False)


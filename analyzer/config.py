"""Конфиг + загрузка секретов.

Приоритет источников: st.secrets (Streamlit Cloud) → .env (локально) → дефолты.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "cache"
CACHE_DIR.mkdir(exist_ok=True)

# Локально подгружаем .env. override=True — потому что иногда shell держит
# пустую переменную с тем же именем, которая бы заблокировала загрузку.
load_dotenv(ROOT / ".env", override=True)

# В Streamlit Cloud секреты доступны через st.secrets. Подтягиваем их в env,
# чтобы остальной код продолжал работать через os.getenv без знания о Streamlit.
try:
    import streamlit as st  # noqa: WPS433
    if hasattr(st, "secrets"):
        for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "SEC_USER_AGENT"):
            try:
                val = st.secrets.get(key)
            except (FileNotFoundError, Exception):  # secrets файла может не быть
                val = None
            if val and not os.getenv(key):
                os.environ[key] = str(val)
except ImportError:
    pass

SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "Anonymous research anon@example.com")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

SEC_HEADERS = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}

import os
from dataclasses import dataclass

from dotenv import find_dotenv, load_dotenv

PLACEHOLDERS = {"", "your-user-id", "your-password"}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Entry:
    project: str
    category: str
    note: str
    hours: float


@dataclass(frozen=True)
class Settings:
    login_url: str
    user_id: str
    password: str
    headless: bool
    verify_ssl: bool
    force_login: bool
    login_timeout: float


def _flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> Settings:
    env_file = find_dotenv(usecwd=True)
    if not env_file:
        raise ConfigError("No .env file found. Copy .env.example to .env and fill it in.")
    load_dotenv(env_file)

    user_id = os.environ.get("ESS_USER_ID", "").strip()
    password = os.environ.get("ESS_PASSWORD", "")
    if user_id in PLACEHOLDERS or password in PLACEHOLDERS:
        raise ConfigError(f"Set ESS_USER_ID and ESS_PASSWORD in {env_file}.")

    return Settings(
        login_url=os.environ.get("ESS_LOGIN_URL", "").strip()
        or "https://live.oxygene.co.ke:8433/ess/web/ess/login",
        user_id=user_id,
        password=password,
        headless=_flag("ESS_HEADLESS", False),
        verify_ssl=_flag("ESS_VERIFY_SSL", False),
        force_login=_flag("ESS_FORCE_LOGIN", False),
        login_timeout=float(os.environ.get("ESS_LOGIN_TIMEOUT", "90")),
    )


def load_entry() -> Entry:
    """The timesheet line to enter; call after load_settings() has read .env."""
    values = {name: os.environ.get(name, "").strip() for name in ("ESS_PROJECT", "ESS_CATEGORY", "ESS_NOTE", "ESS_HOURS")}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ConfigError(f"Set {', '.join(missing)} in .env.")
    try:
        hours = float(values["ESS_HOURS"])
    except ValueError:
        raise ConfigError("ESS_HOURS must be a number, e.g. 8") from None
    return Entry(values["ESS_PROJECT"], values["ESS_CATEGORY"], values["ESS_NOTE"], hours)

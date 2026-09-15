"""Loads non-secret settings from config.yaml.

Trimmed from tableau-admin-dashboard/config.py - drops every setting that only the
sync/scheduler/email-alerting features used (this app has none of those).
"""
import os
import yaml

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")
APP_DATA_DIR = os.path.abspath(
    os.environ.get("APP_DATA_DIR", os.path.join(BASE_DIR, "instance"))
)
INSTANCE_DIR = APP_DATA_DIR
DB_PATH = os.path.join(INSTANCE_DIR, "cache.db")
SECRET_KEY_PATH = os.path.join(INSTANCE_DIR, "secret.key")


class Settings:
    def __init__(self, data):
        self.server_url = os.environ.get("TABLEAU_SERVER_URL", data["server_url"]).rstrip("/")
        self.host = os.environ.get("APP_HOST", data.get("host", "127.0.0.1"))
        self.port = int(os.environ.get("APP_PORT", data.get("port", 5000)))
        sites_env = os.environ.get("TABLEAU_SITES")
        self.sites = (
            [site.strip() for site in sites_env.split(",") if site.strip()]
            if sites_env
            else data["sites"]
        )
        self.default_site = os.environ.get("TABLEAU_DEFAULT_SITE", data.get("default_site", self.sites[0]))


def load_settings():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return Settings(data)


os.makedirs(INSTANCE_DIR, exist_ok=True)
settings = load_settings()

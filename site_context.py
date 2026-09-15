"""Tracks which configured Tableau site is currently active, per browser session.

Used to be a single DB-backed value shared by every browser, which broke as soon
as more than one person used the app at once - switching site in one tab flipped
it under everyone else. Now it's per-session (same idiom as refresh_watch.py), so
each teammate can be looking at a different site independently. Background jobs
(scheduler.py) have no request/session to read, so they take an explicit site
argument instead of calling this module at all.
"""
from flask import session

from config import settings


def get_current_site() -> str:
    site = session.get("current_site")
    return site if site in settings.sites else settings.default_site


def set_current_site(site: str):
    if site in settings.sites:
        session["current_site"] = site

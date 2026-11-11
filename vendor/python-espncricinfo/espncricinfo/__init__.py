"""Python client for ESPNcricinfo match, series, season, team and player data."""

from espncricinfo._browser import close_browser, configure_browser, plan_pages

__all__ = ["close_browser", "configure_browser", "plan_pages"]

"""Live-site structure: dashboard + landing split.

Guards the section skeleton that the motion layer targets: if a section id
is renamed or a page moved, this fails with a clear message instead of the
site silently losing a section. The dashboard lives in site/dashboard.html
(the landing page is the root HTML published as index.html); incidents.html
remains a redirect for old links.
"""

import re
from pathlib import Path

SITE = Path(__file__).resolve().parents[1] / "site"
ROOT = Path(__file__).resolve().parents[1]

DASHBOARD = SITE / "dashboard.html"
LANDING = Path(__file__).resolve().parents[1] / "cross-asset-anomaly-monitor-v6.html"

EXPECTED_SECTIONS = [
    "hero", "overview", "assets", "anomalies",
    "performance", "incidents", "links",
]


def test_dashboard_has_all_sections_in_order():
    html = DASHBOARD.read_text(encoding="utf-8")
    positions = []
    for section_id in EXPECTED_SECTIONS:
        match = re.search(rf'<section class="dash-section" id="{section_id}"', html)
        assert match, f"missing dash section id=\"{section_id}\" in site/dashboard.html"
        positions.append(match.start())
    assert positions == sorted(positions), "sections are out of narrative order"


def test_incidents_page_is_redirect_stub():
    """The standalone incident page was merged into the dashboard;
    the old file remains only as a redirect for existing links."""
    html = (SITE / "incidents.html").read_text(encoding="utf-8")
    assert "dashboard.html#incidents" in html
    assert "data/incidents.json" not in html   # no second data path


def test_dashboard_nav_links_all_sections():
    html = DASHBOARD.read_text(encoding="utf-8")
    # Hero is intentionally absent from the nav: the page loads there.
    # "links" and "performance" are footer-reachable, not in the header nav.
    for section_id in EXPECTED_SECTIONS[1:]:
        if section_id == "links":
            assert 'id="links"' in html, "links section missing from dashboard"
            continue
        assert f'href="#{section_id}"' in html, f"nav missing link to #{section_id}"


def test_dashboard_loads_incident_data():
    js = (SITE / "assets" / "js" / "dashboard.js").read_text(encoding="utf-8")
    assert 'j("data/incidents.json")' in js

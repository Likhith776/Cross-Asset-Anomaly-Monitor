"""Live-site structure: the single-page narrative layout.

Guards the section skeleton that the future animation pass will target:
if a section id is renamed or a page moved, this fails with a clear
message instead of the site silently losing a section.
"""

import re
from pathlib import Path

SITE = Path(__file__).resolve().parents[1] / "site"

EXPECTED_SECTIONS = [
    "hero", "overview", "assets", "anomalies",
    "performance", "incidents", "links",
]


def test_index_has_all_narrative_sections_in_order():
    html = (SITE / "index.html").read_text(encoding="utf-8")
    positions = []
    for section_id in EXPECTED_SECTIONS:
        match = re.search(rf'<section id="{section_id}">', html)
        assert match, f"missing <section id=\"{section_id}\"> in site/index.html"
        positions.append(match.start())
    assert positions == sorted(positions), "sections are out of narrative order"


def test_incidents_page_is_redirect_stub():
    """The standalone incident page was merged into index.html#incidents;
    the old file remains only as a redirect for existing links."""
    html = (SITE / "incidents.html").read_text(encoding="utf-8")
    assert "index.html#incidents" in html
    assert "data/incidents.json" not in html   # no second data path


def test_index_nav_links_all_sections():
    html = (SITE / "index.html").read_text(encoding="utf-8")
    # Hero is intentionally absent from the nav: the page loads there.
    for section_id in EXPECTED_SECTIONS[1:]:
        assert f'href="#{section_id}"' in html, f"nav missing link to #{section_id}"


def test_index_embeds_incident_section_data_source():
    html = (SITE / "index.html").read_text(encoding="utf-8")
    assert 'j("data/incidents.json")' in html

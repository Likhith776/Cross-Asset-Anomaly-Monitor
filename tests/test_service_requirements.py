"""Service packaging: images that ship explain.py must declare requests.

src/detection/explain.py imports `requests` at call time, and its import
chain (anomaly_engine, anomaly_consumer, slim) is shipped by whichever
service images copy src/detection/. If a service's requirements.txt
omits `requests`, setting GEMINI_API_KEY silently never produces an
explanation — the import fails inside explain's broad failure handler
and the only symptom is a generic warning log. This test pins the
contract at the repo level so the gap can't silently reappear when
explain.py's dependency chain grows or a new image starts shipping the
detection tree.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _declared_packages(requirements_path: Path) -> set[str]:
    declared = set()
    for line in requirements_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        declared.add(re.split(r"[=\[<>~!]", line)[0].strip().lower())
    return declared


def test_images_shipping_the_detection_tree_declare_requests():
    covered = False
    for dockerfile in sorted(REPO.glob("src/*/Dockerfile")):
        text = dockerfile.read_text(encoding="utf-8")
        if "COPY src/detection/" not in text:
            continue
        covered = True
        req_match = re.search(r"COPY (src/\w+/requirements\.txt)", text)
        assert req_match, f"{dockerfile}: ships src/detection/ but copies no requirements.txt"
        req_path = REPO / req_match.group(1)
        declared = _declared_packages(req_path)
        assert "requests" in declared, (
            f"{req_match.group(1)} must declare 'requests': this image ships "
            "src/detection/explain.py, which imports requests when an "
            "anomaly explanation is requested"
        )


def test_slim_profile_declares_requests():
    declared = _declared_packages(REPO / "requirements.txt")
    assert "requests" in declared, (
        "requirements.txt (slim profile) must declare 'requests': "
        "src/slim.py imports src/detection/explain.py"
    )

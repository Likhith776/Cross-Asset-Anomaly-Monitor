"""
Publish one live-site cycle: fetch → detect → write artifacts.

Usage (CI): python scripts/publish_live_site.py \
                --state-dir state --output build [--cycles 1]

`--state-dir` holds history.json / anomalies.json between runs (the
workflow checks out the `live-data` branch there and pushes it back).
`--output` receives index.html + data/*.json for Pages.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.livesite import publish


def main() -> None:
    parser = argparse.ArgumentParser(prog="publish_live_site")
    parser.add_argument("--state-dir", default="state")
    parser.add_argument("--output", default="build")
    parser.add_argument("--cycles", type=int, default=1)
    args = parser.parse_args()

    publish(
        state_dir=args.state_dir,
        out_dir=args.output,
        cycles=args.cycles,
    )


if __name__ == "__main__":
    main()

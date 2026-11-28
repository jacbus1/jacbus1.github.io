"""Command line for the reproducible historical ETF comparison."""

import argparse
import json
from pathlib import Path

from .backtest import build_report
from .data import audit_archived_spy, fetch_yahoo, load_prices, provenance_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    prices = parser.add_mutually_exclusive_group(required=True)
    prices.add_argument("--prices", type=Path, help="use an existing normalized local CSV")
    prices.add_argument("--fetch", type=Path, help="fetch Yahoo histories and write a local CSV")
    parser.add_argument("--archive-spy-manifest", type=Path, help="optionally verify SPY against a local archive")
    parser.add_argument("--output", type=Path, required=True, help="write aggregate JSON results")
    args = parser.parse_args()
    price_path = args.fetch or args.prices
    input_paths = (price_path, provenance_path(price_path), args.archive_spy_manifest)
    if args.output.resolve() in {path.resolve() for path in input_paths if path}:
        parser.error("output must differ from input and receipt paths")

    market = fetch_yahoo(args.fetch) if args.fetch else load_prices(args.prices)
    report = build_report(market)
    if args.archive_spy_manifest:
        report["provenance"]["local_spy_archive_overlap"] = audit_archived_spy(price_path, args.archive_spy_manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(f"Wrote {args.output} ({report['holdout']['first_session']} to {report['holdout']['last_session']})")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Compare gtXplorer genotype raw-data (TSV) against EP genotype-json artifacts.

For a given barcode, this script reads:
  <barcode>-genotype-rawdata.tsv.gz       (gtXplorer side)
  results/artifacts/genotype-json/<barcode>.json   (EP side)

It matches records using the CPRA identifier (e.g. "hg19:chr1:100154643:C"),
which is present as the "cpr" column in the TSV and as keys inside the
JSON's "genotype" dict and "missing" list.

For every row in the TSV:
  - If its cpr is a key in json["content"]["genotype"]:
        gt_ep        = genotype value from json
        remarks      = "genotype-json-genotype"
  - elif its cpr is present in json["content"]["missing"]:
        gt_ep        = "."
        remarks      = "genotype-json-missing"
  - else (cpr not found anywhere in the json):
        gt_ep        = ""
        remarks      = "genotype-json-not_found"

Output is written as a TSV with a two-row header (grouping row + column row),
matching the format requested:

        gtxplorer               EP, genotype-json
barcode marker_gtxplorer gt_gtxplorer marker_ep gt_ep remarks/source

Usage:
    python3 compare_genotype.py --barcode 12345678901234
    python3 compare_genotype.py --all
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from common import ARTIFACTS_DIR, OUTPUT_DIR, RAW_DATA_DIR, find_barcodes, open_raw_data, raw_data_path


def load_json_lookup(json_path: Path) -> tuple[dict[str, str], set[str]]:
    """Return (genotype_dict, missing_set) from the genotype-json artifact."""
    with json_path.open(encoding="utf-8") as f:
        data = json.load(f)
    content = data.get("content", {})
    genotype = content.get("genotype", {}) or {}
    missing = set(content.get("missing", []) or [])
    return genotype, missing


def compare_barcode(barcode: str, raw_data_dir: Path = RAW_DATA_DIR,
                    artifacts_dir: Path = ARTIFACTS_DIR) -> Path:
    tsv_path = raw_data_path(barcode, raw_data_dir)
    json_path = artifacts_dir / "genotype-json" / f"{barcode}.json"

    if not json_path.exists():
        raise FileNotFoundError(f"genotype-json artifact not found: {json_path}")

    genotype_lookup, missing_lookup = load_json_lookup(json_path)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{barcode}-gtxplorer_vs_ep.tsv"

    counts = {"genotype": 0, "missing": 0, "not_found": 0}

    with open_raw_data(tsv_path) as fin, \
            out_path.open("w", encoding="utf-8", newline="") as fout:
        reader = csv.DictReader(fin, delimiter="\t")
        writer = csv.writer(fout, delimiter="\t")

        # Two-row header as requested.
        writer.writerow(["", "gtxplorer", "", "EP, genotype-json", "", ""])
        writer.writerow([
            "barcode", "marker_gtxplorer", "gt_gtxplorer",
            "marker_ep", "gt_ep", "remarks/source",
        ])

        for row in reader:
            cpr = row.get("cpr", "")
            gt_gtxplorer = row.get("GT", "")

            if cpr in genotype_lookup:
                marker_ep = cpr
                gt_ep = genotype_lookup[cpr]
                remark = "genotype-json-genotype"
                counts["genotype"] += 1
            elif cpr in missing_lookup:
                marker_ep = cpr
                gt_ep = "."
                remark = "genotype-json-missing"
                counts["missing"] += 1
            else:
                marker_ep = ""
                gt_ep = ""
                remark = "genotype-json-not_found"
                counts["not_found"] += 1

            writer.writerow([
                barcode, cpr, gt_gtxplorer, marker_ep, gt_ep, remark,
            ])

    total = sum(counts.values())
    print(f"[{barcode}] wrote {out_path} "
          f"(total={total}, genotype={counts['genotype']}, "
          f"missing={counts['missing']}, not_found={counts['not_found']})")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare gtXplorer genotype rawdata TSV vs EP genotype-json."
    )
    parser.add_argument("--barcode", help="Single barcode to process, e.g. 12345678901234")
    parser.add_argument("--all", action="store_true", help="Process every barcode found in the raw data directory")
    parser.add_argument("--raw-data-dir", type=Path, default=RAW_DATA_DIR)
    parser.add_argument("--artifacts-dir", type=Path, default=ARTIFACTS_DIR)
    args = parser.parse_args()

    if not args.barcode and not args.all:
        available = find_barcodes(args.raw_data_dir)
        parser.error(
            "Provide --barcode <BARCODE> or --all.\n"
            f"Available barcodes: {', '.join(available) if available else '(none found)'}"
        )

    barcodes = find_barcodes(args.raw_data_dir) if args.all else [args.barcode]

    exit_code = 0
    for barcode in barcodes:
        try:
            compare_barcode(barcode, args.raw_data_dir, args.artifacts_dir)
        except FileNotFoundError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

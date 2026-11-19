#!/usr/bin/env python3
"""
Reverse check: for every category found in EP's json artifacts (every folder
under results/artifacts except genotype-json), verify whether that same
category/globalkey also exists in gtXplorer's <barcode>-genotype-rawdata.tsv.

This is the mirror image of compare_category_level_score.py (which starts
from the gtXplorer TSV and looks for matches in EP). Here we start from EP
and look for matches in gtXplorer, and write the result to a separate sheet
(a new, distinct output file) so it doesn't overwrite the existing
gtxplorer-to-EP comparison.

Output: results/comparison/<barcode>-ep_vs_gtxplorer_category.tsv
One row per (EP source file, category key) pair.

Usage:
    python3 compare_ep_categories_in_gtxplorer.py --barcode 27117164714547
    python3 compare_ep_categories_in_gtxplorer.py --all
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

# Reuse the EP-index / TSV-index builders already defined for the
# gtXplorer -> EP comparison so both scripts stay in sync.
from compare_category_level_score import (
    BASE_DIR,
    build_ep_index,
    build_tsv_categories,
    find_barcodes,
)

OUTPUT_DIR = BASE_DIR / "results" / "comparison"


def compare_barcode(barcode: str) -> Path:
    tsv_path = BASE_DIR / f"{barcode}-genotype-rawdata.tsv"
    if not tsv_path.exists():
        raise FileNotFoundError(f"Raw data TSV not found: {tsv_path}")

    tsv_categories = build_tsv_categories(tsv_path)
    ep_index = build_ep_index(barcode)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{barcode}-ep_vs_gtxplorer_category.tsv"

    counts = {"found": 0, "not_found": 0}

    # Build rows: one per (source_file, globalkey) EP entry, sorted for
    # stable/readable output (source file, then key name).
    rows = []
    for globalkey, entries in ep_index.items():
        for entry in entries:
            rows.append((entry["source_file"], globalkey, entry))
    rows.sort(key=lambda r: (r[0], r[1]))

    with out_path.open("w", encoding="utf-8", newline="") as fout:
        writer = csv.writer(fout, delimiter="\t")
        writer.writerow(["", "", "EP", "", "", "gtxplorer", "", ""])
        writer.writerow([
            "barcode",
            "ep_source_file", "category_ep", "level_ep", "score_ep",
            "category_gtxplorer", "level_gtxplorer", "score_gtxplorer",
            "remarks/source",
        ])

        for source_file, globalkey, entry in rows:
            tsv_info = tsv_categories.get(globalkey)
            if tsv_info is not None:
                category_gtxplorer = tsv_info["category"]
                level_gtxplorer = tsv_info["level"]
                score_gtxplorer = tsv_info["score"]
                remark = "found_in_gtxplorer"
                counts["found"] += 1
            else:
                category_gtxplorer = ""
                level_gtxplorer = ""
                score_gtxplorer = ""
                remark = "not_found_in_gtxplorer"
                counts["not_found"] += 1

            writer.writerow([
                barcode,
                source_file, globalkey, entry["level"], entry["score"],
                category_gtxplorer, level_gtxplorer, score_gtxplorer,
                remark,
            ])

    total = sum(counts.values())
    print(f"[{barcode}] wrote {out_path} "
          f"(ep_categories={total}, found_in_gtxplorer={counts['found']}, "
          f"not_found_in_gtxplorer={counts['not_found']})")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check which EP json categories are also present in gtXplorer rawdata TSV."
    )
    parser.add_argument("--barcode", help="Single barcode to process, e.g. 27117164714547")
    parser.add_argument("--all", action="store_true", help="Process every barcode found in this folder")
    args = parser.parse_args()

    if not args.barcode and not args.all:
        available = find_barcodes()
        parser.error(
            "Provide --barcode <BARCODE> or --all.\n"
            f"Available barcodes: {', '.join(available) if available else '(none found)'}"
        )

    barcodes = find_barcodes() if args.all else [args.barcode]

    exit_code = 0
    for barcode in barcodes:
        try:
            compare_barcode(barcode)
        except FileNotFoundError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

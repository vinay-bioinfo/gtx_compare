#!/usr/bin/env python3
"""
Compare gtXplorer genotype raw-data (TSV) against EP's category-level JSON
artifacts (every artifact type EXCEPT genotype-json).

For each barcode:
  gtXplorer side : <barcode>-genotype-rawdata.tsv
  EP side        : results/artifacts/<any-folder-except-genotype-json>/<barcode>.json

Matching strategy
------------------
Each row of the TSV belongs to a trait/category identified by the
"globalkey" column (e.g. "hypertension", "obesity", "iron"). The TSV
repeats the same category-level "level" and "level_score" (numeric score)
for every marker (cpr) that contributes to that category.

Each EP json artifact (apart from genotype-json) stores its content as a
dict keyed by that same "globalkey" style name, e.g.:

    "obesity": {
        "level": "normal",
        "score": 273,
        "missing": ["hg19:chr18:57673799:A", ...],
        "confidence": 3
    }

Only files/keys where the value is a dict containing a "level" entry are
considered valid EP category nodes (this naturally excludes genotype-json,
ancestry-json, cnv-auto-json, metadata-json, disease-cancer-only-json,
disease-carrier-only-json and drugs-json footnote-only entries, since none
of those expose a per-trait {level, score} pair the same way).

To make sure a key match is a *real* match (and not a coincidental key name
collision across unrelated files), we cross-check using the cpra list: the
set of "cpr" values that contribute to that category in the TSV is
intersected against the EP node's own marker set (its "missing" list plus
the keys of its "genotype" dict, when present). When more than one EP file
exposes the same category key, the one with overlapping cpra evidence wins;
otherwise the first match found is used.

Output format (one row per unique category/globalkey found in the TSV):

        gtxplorer                       EP
barcode category_gtxplorer level_gtxplorer score_gtxplorer category_ep level_ep score_ep ep_source_file

Usage:
    python3 compare_category_level_score.py --barcode 27117164714547
    python3 compare_category_level_score.py --all
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ARTIFACTS_DIR = BASE_DIR / "results" / "artifacts"
OUTPUT_DIR = BASE_DIR / "results" / "comparison"

EXCLUDED_ARTIFACT_DIRS = {"genotype-json"}


def find_barcodes() -> list[str]:
    """Return all barcodes for which a *-genotype-rawdata.tsv file exists."""
    return sorted(
        p.name.split("-genotype-rawdata.tsv")[0]
        for p in BASE_DIR.glob("*-genotype-rawdata.tsv")
    )


def candidate_artifact_dirs() -> list[Path]:
    """All artifact sub-folders except genotype-json."""
    if not ARTIFACTS_DIR.is_dir():
        return []
    return sorted(
        d for d in ARTIFACTS_DIR.iterdir()
        if d.is_dir() and d.name not in EXCLUDED_ARTIFACT_DIRS
    )


def node_marker_set(node: dict) -> set[str]:
    """Collect the cpra identifiers referenced by an EP category node."""
    markers: set[str] = set()
    missing = node.get("missing")
    if isinstance(missing, list):
        markers.update(str(m) for m in missing)
    genotype = node.get("genotype")
    if isinstance(genotype, dict):
        markers.update(genotype.keys())
    return markers


def build_ep_index(barcode: str) -> dict[str, list[dict]]:
    """
    Build: globalkey -> list of {source_file, level, score, markers(set)}
    across every non-genotype artifact json available for this barcode.
    """
    index: dict[str, list[dict]] = {}
    for art_dir in candidate_artifact_dirs():
        json_path = art_dir / f"{barcode}.json"
        if not json_path.exists():
            continue
        try:
            with json_path.open(encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"WARNING: could not read {json_path}: {e}", file=sys.stderr)
            continue

        content = data.get("content", {})
        if not isinstance(content, dict):
            continue

        for key, val in content.items():
            if isinstance(val, dict) and "level" in val:
                entry = {
                    "source_file": art_dir.name,
                    "level": val.get("level", ""),
                    "score": val.get("score", ""),
                    "markers": node_marker_set(val),
                }
                index.setdefault(key, []).append(entry)
    return index


def build_tsv_categories(tsv_path: Path) -> "dict[str, dict]":
    """
    Dedupe TSV rows by globalkey, keeping:
      category (display CATEGORY name), level, score (level_score column),
      and the full set of cpr values seen for that globalkey.
    """
    categories: dict[str, dict] = {}
    with tsv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            globalkey = row.get("globalkey", "")
            if not globalkey:
                continue
            cpr = row.get("cpr", "")
            if globalkey not in categories:
                categories[globalkey] = {
                    "category": row.get("CATEGORY", ""),
                    "level": row.get("level", ""),
                    "score": row.get("level_score", ""),
                    "cpras": set(),
                }
            if cpr:
                categories[globalkey]["cpras"].add(cpr)
    return categories


def pick_best_match(candidates: list[dict], tsv_cpras: set[str]) -> dict | None:
    """Prefer the EP candidate whose marker set overlaps the TSV's cpra set."""
    if not candidates:
        return None
    for c in candidates:
        if c["markers"] & tsv_cpras:
            return c
    return candidates[0]


def compare_barcode(barcode: str) -> Path:
    tsv_path = BASE_DIR / f"{barcode}-genotype-rawdata.tsv"
    if not tsv_path.exists():
        raise FileNotFoundError(f"Raw data TSV not found: {tsv_path}")

    tsv_categories = build_tsv_categories(tsv_path)
    ep_index = build_ep_index(barcode)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{barcode}-gtxplorer_vs_ep_category.tsv"

    counts = {"matched": 0, "not_found": 0}

    with out_path.open("w", encoding="utf-8", newline="") as fout:
        writer = csv.writer(fout, delimiter="\t")
        writer.writerow(["", "gtxplorer", "", "", "EP", "", "", ""])
        writer.writerow([
            "barcode",
            "category_gtxplorer", "level_gtxplorer", "score_gtxplorer",
            "category_ep", "level_ep", "score_ep", "ep_source_file",
        ])

        for globalkey, info in sorted(tsv_categories.items()):
            candidates = ep_index.get(globalkey, [])
            best = pick_best_match(candidates, info["cpras"])

            if best is not None:
                category_ep = globalkey
                level_ep = best["level"]
                score_ep = best["score"]
                ep_source_file = best["source_file"]
                counts["matched"] += 1
            else:
                category_ep = ""
                level_ep = ""
                score_ep = ""
                ep_source_file = "not_found"
                counts["not_found"] += 1

            writer.writerow([
                barcode,
                info["category"], info["level"], info["score"],
                category_ep, level_ep, score_ep, ep_source_file,
            ])

    total = sum(counts.values())
    print(f"[{barcode}] wrote {out_path} "
          f"(categories={total}, matched={counts['matched']}, not_found={counts['not_found']})")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare gtXplorer category/level/score vs EP non-genotype json artifacts."
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

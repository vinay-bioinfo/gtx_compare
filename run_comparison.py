#!/usr/bin/env python3
"""Fetch EP artifacts, run comparisons, and create a workbook per barcode."""

import argparse
import csv
import fcntl
import os
import sys
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import URLError
from zipfile import BadZipFile

from openpyxl import Workbook, load_workbook
from openpyxl.utils.exceptions import IllegalCharacterError

import compare_category_level_score
import compare_ep_categories_in_gtxplorer
import compare_genotype
from common import ARTIFACTS_DIR, RAW_DATA_DIR, raw_data_path
from fetch_artifacts import DEFAULT_CONFIG, fetch_artifacts, validate_barcode

SHEET_NAMES = ("genotype_comparison", "category_comparison", "ep_cateogory_comparison")
UK_ARTIFACTS = {"uk-vital-json", "uk-population-health-disease-risk-neuropsychiatric-json"}
STANDARD_ARTIFACTS = {
    "vital-json", "vital-lite-json", "population-health-disease-risk-neuropsychiatric-json",
}


def write_row(sheet, row_number: int, row: list[str], source: Path) -> None:
    if row_number > 1048576 or len(row) > 16384:
        raise ValueError(f"{source} exceeds Excel worksheet size limits")
    for column, value in enumerate(row, 1):
        if len(value) > 32767:
            raise ValueError(f"{source} contains a cell exceeding Excel's 32767 character limit")
        try:
            cell = sheet.cell(row=row_number, column=column, value=value)
            cell.data_type = "s"
        except IllegalCharacterError as exc:
            raise ValueError(f"{source} contains a character not supported by Excel") from exc


def save_workbook(workbook: Workbook, output: Path) -> None:
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".xlsx", delete=False) as file:
        temporary = Path(file.name)
    try:
        workbook.save(temporary)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def create_workbook(barcode: str, genotype: Path, category: Path, reverse: Path) -> Path:
    output = genotype.parent / f"{barcode}-comparison.xlsx"
    workbook = Workbook()
    workbook.remove(workbook.active)
    for title, path in zip(SHEET_NAMES, (genotype, category, reverse)):
        sheet = workbook.create_sheet(title)
        with path.open(encoding="utf-8-sig", newline="") as file:
            for row_number, row in enumerate(csv.reader(file, delimiter="\t"), 1):
                write_row(sheet, row_number, row, path)
    save_workbook(workbook, output)
    print(f"[{barcode}] wrote {output}")
    return output


def scores_equal(left: str, right: str) -> bool:
    try:
        a, b = Decimal(left), Decimal(right)
        if a.is_finite() and b.is_finite():
            return a == b
    except InvalidOperation:
        pass
    return left == right


def is_mismatch(sheet_name: str, row: list[str]) -> bool:
    status = row[-1].strip().lower()
    if not status or any(word in status for word in ("not_found", "not found", "missing", "mismatch")):
        return True
    if sheet_name == "genotype_comparison":
        return any(not row[index].strip() or row[index].strip() == "." for index in (1, 2, 3, 4)) or (
            row[1] != row[3] or row[2] != row[4]
        )
    if sheet_name == "category_comparison":
        if not row[1].strip() or not row[4].strip():
            return True
        left_level, right_level, left_score, right_score = row[2], row[5], row[3], row[6]
    else:
        if not row[2].strip() or not row[5].strip():
            return True
        left_level, right_level, left_score, right_score = row[3], row[6], row[4], row[7]
    return (not all(value.strip() for value in (left_level, right_level, left_score, right_score))
            or left_level != right_level or not scores_equal(left_score, right_score))


def update_mismatched_workbook(barcode: str, genotype: Path, category: Path, reverse: Path) -> Path:
    paths = (genotype, category, reverse)
    output = genotype.parent / "mismatched_merged.xlsx"
    lock_path = output.with_suffix(".xlsx.lock")
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = None
        if output.exists():
            try:
                previous = load_workbook(output, read_only=True)
            except BadZipFile as exc:
                raise ValueError(f"Invalid merged workbook: {output}") from exc
            if previous.sheetnames != list(SHEET_NAMES):
                previous.close()
                raise ValueError(f"Unexpected sheets in merged workbook: {output}")
        try:
            workbook = Workbook()
            workbook.remove(workbook.active)
            for title, path in zip(SHEET_NAMES, paths):
                sheet = workbook.create_sheet(title)
                with path.open(encoding="utf-8-sig", newline="") as file:
                    reader = csv.reader(file, delimiter="\t")
                    headers = [next(reader, None), next(reader, None)]
                    if any(header is None for header in headers):
                        raise ValueError(f"Missing comparison headers in {path}")
                    width = len(headers[1])
                    for row_number, header in enumerate(headers, 1):
                        write_row(sheet, row_number, header, path)
                    next_row = 3
                    if previous is not None:
                        old_sheet = previous[title]
                        old_rows = old_sheet.iter_rows(values_only=True)
                        old_headers = [next(old_rows, None), next(old_rows, None)]
                        for old, new in zip(old_headers, headers):
                            if old is None or list(old[:width]) != [value or None for value in new] + [None] * (width - len(new)):
                                raise ValueError(f"Headers differ in {output}, sheet {title}")
                        if old_sheet.max_column > width:
                            raise ValueError(f"Unexpected columns in {output}, sheet {title}")
                        for old in old_rows:
                            values = [str(value) if value is not None else "" for value in old]
                            if values and values[0] != barcode:
                                write_row(sheet, next_row, values, output)
                                next_row += 1
                    for row in reader:
                        if len(row) != width or row[0] != barcode:
                            raise ValueError(f"Unexpected comparison row in {path}")
                        if is_mismatch(title, row):
                            write_row(sheet, next_row, row, path)
                            next_row += 1
            save_workbook(workbook, output)
        finally:
            if previous is not None:
                previous.close()
    print(f"[{barcode}] updated {output}")
    return output


def print_summary(barcode: str, artifacts: set[str], paths: tuple[Path, Path, Path]) -> None:
    has_uk = bool(artifacts & UK_ARTIFACTS)
    has_standard = bool(artifacts & STANDARD_ARTIFACTS)
    if has_uk and has_standard:
        variant = "mixed UK and standard"
    elif has_uk:
        variant = "UK"
    elif has_standard:
        variant = "standard"
    else:
        variant = "undetermined"
    print(f"[{barcode}] Summary: artifact variant={variant}")
    print(f"[{barcode}] Downloaded artifacts: {', '.join(sorted(artifacts))}")
    for sheet_name, path in zip(SHEET_NAMES, paths):
        total = mismatched = 0
        with path.open(encoding="utf-8-sig", newline="") as file:
            reader = csv.reader(file, delimiter="\t")
            next(reader)
            next(reader)
            for row in reader:
                total += 1
                mismatched += is_mismatch(sheet_name, row)
        print(f"[{barcode}] {sheet_name}: total={total}, matched={total - mismatched}, "
              f"mismatched/missing={mismatched}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--barcode", nargs="+", help="One or more barcodes")
    source.add_argument("--barcode-file", type=Path, help="One barcode per line; optional barcode header")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--raw-data-dir", type=Path, default=RAW_DATA_DIR)
    parser.add_argument("--artifacts-dir", type=Path, default=ARTIFACTS_DIR)
    args = parser.parse_args()

    try:
        if args.barcode_file:
            lines = args.barcode_file.read_text(encoding="utf-8-sig").splitlines()
            barcodes = [line.strip() for line in lines if line.strip()]
            if barcodes and barcodes[0].lower() == "barcode":
                barcodes.pop(0)
        else:
            barcodes = args.barcode
        if not barcodes:
            raise ValueError("No barcodes supplied")
        barcodes = list(dict.fromkeys(validate_barcode(barcode) for barcode in barcodes))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    failed = False
    for barcode in barcodes:
        try:
            raw_data_path(barcode, args.raw_data_dir)
            artifacts = fetch_artifacts(barcode, args.config, args.artifacts_dir)
            if not compare_category_level_score.build_ep_index(barcode, args.artifacts_dir, artifacts):
                raise ValueError("No category-level data found in downloaded artifacts")
            genotype = compare_genotype.compare_barcode(barcode, args.raw_data_dir, args.artifacts_dir)
            category = compare_category_level_score.compare_barcode(barcode, args.raw_data_dir, args.artifacts_dir, artifacts)
            reverse = compare_ep_categories_in_gtxplorer.compare_barcode(barcode, args.raw_data_dir, args.artifacts_dir, artifacts)
            create_workbook(barcode, genotype, category, reverse)
            update_mismatched_workbook(barcode, genotype, category, reverse)
            print_summary(barcode, artifacts, (genotype, category, reverse))
        except (ValueError, KeyError, OSError, URLError) as exc:
            print(f"ERROR [{barcode}]: {exc}", file=sys.stderr)
            failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())

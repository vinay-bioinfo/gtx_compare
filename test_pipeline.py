import csv
import gzip
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from openpyxl import load_workbook

import compare_category_level_score
import compare_ep_categories_in_gtxplorer
import compare_genotype
import fetch_artifacts
import run_comparison
from common import RAW_DATA_DIR, find_barcodes, raw_data_path


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = self.root / "raw"
        self.raw.mkdir()
        self.artifacts = self.root / "artifacts"
        self.output = self.root / "comparison"
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps({
            "use_production": False,
            "artifact_types": ["genotype-json", "vital-json", "drugs-json"],
            "user_agent": "test",
            "urls": {"testing": {"front": "front.example", "back": "back.example",
                                  "domain": "example"}},
        }))
        for module in (compare_genotype, compare_category_level_score,
                       compare_ep_categories_in_gtxplorer):
            patcher = patch.object(module, "OUTPUT_DIR", self.output)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_raw(self, barcode):
        with gzip.open(self.raw / f"{barcode}-genotype-rawdata.tsv.gz", "wt", encoding="utf-8") as file:
            file.write("cpr\tGT\tglobalkey\tCATEGORY\tlevel\tlevel_score\n"
                       "marker1\tAA\ttrait\tTrait\thigh\t2\n")

    def response(self, url, headers):
        if "/kit/v1.0/barcode/" in url:
            return {"kitId": "kit", "test": [
                {"status": "complete", "datetime": "2024-01-01", "artifacts": []},
                {"status": "complete", "datetime": "2025-01-01", "artifacts": [
                    {"artifactId": "genotype", "artifact": {"name": "genotype-json"}},
                    {"artifactId": "vital", "artifact": {"name": "vital-json"}},
                ]},
                {"status": "test-terminated", "datetime": "2026-01-01", "artifacts": []},
            ]}
        if url.endswith("/genotype"):
            return {"content": {"genotype": {"marker1": "AA"}, "missing": []}}
        if url.endswith("/vital"):
            return {"content": {"trait": {"level": "high", "score": 2}}}
        raise AssertionError(url)

    def test_pipeline_and_stale_artifact_exclusion(self):
        self.write_raw("123")
        stale = self.artifacts / "drugs-json" / "123.json"
        stale.parent.mkdir(parents=True)
        stale.write_text('{"content":{"stale":{"level":"low"}}}')
        self.assertEqual(find_barcodes(self.raw), ["123"])
        env_file = self.root / ".env"
        env_file.write_text("AUTH_BEARER=file-token\n")
        argv = ["run_comparison.py", "--barcode", "123", "--config", str(self.config),
                "--raw-data-dir", str(self.raw), "--artifacts-dir", str(self.artifacts)]
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(fetch_artifacts, "ENV_FILE", env_file), \
             patch.object(fetch_artifacts, "read_json", side_effect=self.response), \
             patch("sys.argv", argv):
            self.assertEqual(run_comparison.main(), 0)
        with (self.output / "123-gtxplorer_vs_ep.tsv").open() as file:
            genotype = list(csv.reader(file, delimiter="\t"))
        self.assertEqual(genotype[2], ["123", "marker1", "AA", "marker1", "AA",
                                       "genotype-json-genotype"])
        with (self.output / "123-gtxplorer_vs_ep_category.tsv").open() as file:
            forward = list(csv.reader(file, delimiter="\t"))
        self.assertEqual(forward[2], ["123", "Trait", "high", "2", "trait", "high", "2", "vital-json"])
        with (self.output / "123-ep_vs_gtxplorer_category.tsv").open() as file:
            reverse = list(csv.reader(file, delimiter="\t"))
        self.assertEqual(len(reverse), 3)
        self.assertEqual(reverse[2][-1], "found_in_gtxplorer")
        workbook = load_workbook(self.output / "123-comparison.xlsx")
        self.addCleanup(workbook.close)
        self.assertEqual(workbook.sheetnames, [
            "genotype_comparison", "category_comparison", "ep_cateogory_comparison"])
        for sheet, expected in zip(workbook.worksheets, (genotype, forward, reverse)):
            actual = list(sheet.values)
            self.assertEqual(actual, [
                tuple(value or None for value in row) + (None,) * (sheet.max_column - len(row))
                for row in expected
            ])
        merged = load_workbook(self.output / "mismatched_merged.xlsx")
        self.addCleanup(merged.close)
        self.assertEqual(merged.sheetnames, workbook.sheetnames)
        self.assertTrue(all(sheet.max_row == 2 for sheet in merged.worksheets))

    def test_missing_required_artifact_does_not_write(self):
        def metadata_only(url, headers):
            return {"kitId": "kit", "test": [{"datetime": "2025", "artifacts": []}]}

        with patch.dict(os.environ, {"AUTH_BEARER": "test-token"}), \
             patch.object(fetch_artifacts, "read_json", side_effect=metadata_only):
            with self.assertRaisesRegex(ValueError, "genotype-json"):
                fetch_artifacts.fetch_artifacts("123", self.config, self.artifacts)
        self.assertFalse(self.artifacts.exists())

    def test_uk_and_standard_samples_fetch_compare_and_report(self):
        config = json.loads(self.config.read_text())
        config["artifact_types"] = [
            "genotype-json", "uk-vital-json",
            "uk-population-health-disease-risk-neuropsychiatric-json",
            "vital-json", "population-health-disease-risk-neuropsychiatric-json",
        ]
        self.config.write_text(json.dumps(config))
        default_types = json.loads(fetch_artifacts.DEFAULT_CONFIG.read_text())["artifact_types"]
        self.assertIn("uk-vital-json", default_types)
        self.assertIn("uk-population-health-disease-risk-neuropsychiatric-json", default_types)
        self.write_raw("123")
        self.write_raw("456")

        def response(url, headers):
            if "/kit/v1.0/barcode/" in url:
                uk = "/123?" in url
                names = ["genotype-json", "uk-vital-json",
                         "uk-population-health-disease-risk-neuropsychiatric-json"] if uk else [
                             "genotype-json", "vital-json"]
                return {"kitId": "uk" if uk else "standard", "test": [{
                    "datetime": "2025", "artifacts": [
                        {"artifactId": name, "artifact": {"name": name}} for name in names
                    ]}]}
            if url.endswith("/genotype-json"):
                return {"content": {"genotype": {"marker1": "AG" if "/uk/" in url else "AA"}}}
            if url.endswith("/uk-vital-json"):
                return {"content": {"trait": {"level": "low", "score": 2}}}
            if url.endswith("/uk-population-health-disease-risk-neuropsychiatric-json"):
                return {"content": {"other": {"level": "normal", "score": 1}}}
            if url.endswith("/vital-json"):
                return {"content": {"trait": {"level": "high", "score": 2}}}
            raise AssertionError(url)

        argv = ["run_comparison.py", "--barcode", "123", "456", "--config", str(self.config),
                "--raw-data-dir", str(self.raw), "--artifacts-dir", str(self.artifacts)]
        stdout = io.StringIO()
        with patch.dict(os.environ, {"AUTH_BEARER": "test-token"}), \
             patch.object(fetch_artifacts, "read_json", side_effect=response), \
             patch("sys.argv", argv), redirect_stdout(stdout):
            self.assertEqual(run_comparison.main(), 0)
        for name in ("uk-vital-json", "uk-population-health-disease-risk-neuropsychiatric-json"):
            self.assertTrue((self.artifacts / name / "123.json").is_file())
            self.assertIn(name, stdout.getvalue())
        text = stdout.getvalue()
        self.assertIn("[123] Summary: artifact variant=UK", text)
        self.assertIn("[456] Summary: artifact variant=standard", text)
        self.assertIn("[123] genotype_comparison: total=1, matched=0, mismatched/missing=1", text)
        self.assertIn("[123] category_comparison: total=1, matched=0, mismatched/missing=1", text)
        self.assertIn("[123] ep_cateogory_comparison: total=2, matched=0, mismatched/missing=2", text)
        self.assertIn("[456] genotype_comparison: total=1, matched=1, mismatched/missing=0", text)
        merged = load_workbook(self.output / "mismatched_merged.xlsx")
        self.addCleanup(merged.close)
        self.assertEqual(merged["genotype_comparison"].max_row, 3)
        self.assertEqual(merged["category_comparison"].cell(3, 8).value, "uk-vital-json")
        self.assertEqual(merged["ep_cateogory_comparison"].max_row, 4)

    def test_summary_distinguishes_mixed_and_undetermined_artifacts(self):
        paths = tuple(self.root / f"{index}.tsv" for index in range(3))
        for path in paths:
            path.write_text("group\nheader\n")
        for artifacts, expected in (
            ({"uk-vital-json", "vital-json"}, "mixed UK and standard"),
            ({"genotype-json", "drugs-json"}, "undetermined"),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                run_comparison.print_summary("123", artifacts, paths)
            self.assertIn(f"artifact variant={expected}", stdout.getvalue())

    def test_batch_continues_after_missing_input(self):
        self.write_raw("123")
        barcode_file = self.root / "barcodes.txt"
        barcode_file.write_text("barcode\n456\n123\n123\n")
        argv = ["run_comparison.py", "--barcode-file", str(barcode_file),
                "--config", str(self.config), "--raw-data-dir", str(self.raw),
                "--artifacts-dir", str(self.artifacts)]
        with patch.dict(os.environ, {"AUTH_BEARER": "test-token"}), \
             patch.object(fetch_artifacts, "read_json", side_effect=self.response), \
             patch("sys.argv", argv):
            self.assertEqual(run_comparison.main(), 1)
        self.assertTrue((self.output / "123-gtxplorer_vs_ep.tsv").exists())
        self.assertTrue((self.output / "123-comparison.xlsx").exists())
        self.assertFalse((self.output / "456-comparison.xlsx").exists())
        self.assertTrue((self.output / "mismatched_merged.xlsx").exists())

    def test_merged_mismatches_accumulate_and_replace_barcode(self):
        paths = [self.root / f"{i}.tsv" for i in range(3)]
        headers = [
            [["", "gtxplorer", "", "EP", "", ""],
             ["barcode", "marker_gtxplorer", "gt_gtxplorer", "marker_ep", "gt_ep", "remarks/source"]],
            [["", "gtxplorer", "", "", "EP", "", "", ""],
             ["barcode", "category_gtxplorer", "level_gtxplorer", "score_gtxplorer",
              "category_ep", "level_ep", "score_ep", "ep_source_file"]],
            [["", "", "EP", "", "", "gtxplorer", "", ""],
             ["barcode", "ep_source_file", "category_ep", "level_ep", "score_ep",
              "category_gtxplorer", "level_gtxplorer", "score_gtxplorer", "remarks/source"]],
        ]

        def write_comparisons(barcode, mismatches):
            rows = [
                [[barcode, "marker", "AA", "marker", "AA", "genotype-json-genotype"],
                 [barcode, "different", "AA", "different", "AG", "genotype-json-genotype"],
                 [barcode, "missing", "AA", "missing", ".", "genotype-json-missing"],
                 [barcode, "absent", "AA", "", "", "genotype-json-not_found"],
                 [barcode, "empty-status", "AA", "empty-status", "AA", ""]],
                [[barcode, "same", "normal", "1.0", "same", "normal", "1", "vital-json"],
                 [barcode, "level", "normal", "1", "level", "high", "1", "vital-json"],
                 [barcode, "score", "normal", "1", "score", "normal", "2", "vital-json"],
                 [barcode, "missing", "normal", "1", "", "", "", "not_found"]],
                [[barcode, "vital", "same", "normal", "1", "same", "normal", "1.0", "found_in_gtxplorer"],
                 [barcode, "vital", "different", "high", "1", "different", "normal", "1", "found_in_gtxplorer"],
                 [barcode, "vital", "empty", "high", "1", "empty", "high", "", "found_in_gtxplorer"],
                 [barcode, "vital", "absent", "high", "1", "", "", "", "not_found_in_gtxplorer"]],
            ]
            for path, header, data in zip(paths, headers, rows):
                with path.open("w", newline="") as file:
                    csv.writer(file, delimiter="\t").writerows(header + (data if mismatches else data[:1]))

        write_comparisons("123", True)
        output = run_comparison.update_mismatched_workbook("123", *paths)
        write_comparisons("456", True)
        run_comparison.update_mismatched_workbook("456", *paths)
        workbook = load_workbook(output)
        for sheet in workbook.worksheets:
            mismatch_count = 4 if sheet.title == "genotype_comparison" else 3
            self.assertEqual(sheet.max_row, 2 + mismatch_count * 2)
            self.assertEqual([sheet.cell(row, 1).value for row in range(3, sheet.max_row + 1)],
                             ["123"] * mismatch_count + ["456"] * mismatch_count)
        workbook.close()
        write_comparisons("123", False)
        run_comparison.update_mismatched_workbook("123", *paths)
        workbook = load_workbook(output)
        for sheet in workbook.worksheets:
            mismatch_count = 4 if sheet.title == "genotype_comparison" else 3
            self.assertEqual(sheet.max_row, 2 + mismatch_count)
            self.assertEqual([sheet.cell(row, 1).value for row in range(3, sheet.max_row + 1)],
                             ["456"] * mismatch_count)
        workbook.close()
        original = output.read_bytes()
        paths[1].write_text("bad header\n")
        with self.assertRaisesRegex(ValueError, "headers"):
            run_comparison.update_mismatched_workbook("123", *paths)
        self.assertEqual(output.read_bytes(), original)

    def test_empty_category_is_mismatch_even_when_levels_and_scores_match(self):
        self.assertTrue(run_comparison.is_mismatch(
            "category_comparison", ["123", "", "normal", "1", "trait", "normal", "1", "vital"]))
        self.assertTrue(run_comparison.is_mismatch(
            "ep_cateogory_comparison", ["123", "vital", "trait", "normal", "1", "", "normal", "1", "found"]))

    def test_workbook_preserves_text_and_rejects_oversized_cells(self):
        paths = [self.root / f"{i}.tsv" for i in range(3)]
        for path in paths:
            path.write_text('barcode\tvalue\n00123\t=1+1\n')
        output = run_comparison.create_workbook("123", *paths)
        workbook = load_workbook(output)
        self.assertEqual(workbook.worksheets[0]["A2"].value, "00123")
        self.assertEqual(workbook.worksheets[0]["B2"].value, "=1+1")
        self.assertEqual(workbook.worksheets[0]["B2"].data_type, "s")
        workbook.close()
        original = output.read_bytes()
        paths[1].write_text("x" * 32768 + "\n")
        with self.assertRaisesRegex(ValueError, "32767"):
            run_comparison.create_workbook("123", *paths)
        self.assertEqual(output.read_bytes(), original)

    def test_rejects_path_in_barcode(self):
        with self.assertRaisesRegex(ValueError, "digits only"):
            fetch_artifacts.validate_barcode("../123")

    def test_fetcher_uses_dotenv_and_environment_override(self):
        env_file = self.root / ".env"
        env_file.write_text('# comment\nexport AUTH_BEARER="file-token"\n')
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(fetch_artifacts, "ENV_FILE", env_file), \
             patch.object(fetch_artifacts, "read_json", side_effect=self.response) as request:
            fetch_artifacts.fetch_artifacts("123", self.config, self.artifacts)
            self.assertEqual(request.call_args_list[0].args[1]["authorization"], "Bearer file-token")
            with patch.dict(os.environ, {"AUTH_BEARER": "environment-token"}):
                fetch_artifacts.fetch_artifacts("123", self.config, self.artifacts)
                self.assertEqual(request.call_args_list[-1].args[1]["authorization"],
                                 "Bearer environment-token")

    def test_missing_or_invalid_dotenv_token(self):
        env_file = self.root / ".env"
        with patch.dict(os.environ, {}, clear=True), patch.object(fetch_artifacts, "ENV_FILE", env_file):
            with self.assertRaisesRegex(ValueError, "Set AUTH_BEARER"):
                fetch_artifacts.get_auth_bearer()
            env_file.write_text('AUTH_BEARER="unterminated\n')
            with self.assertRaisesRegex(ValueError, "Invalid AUTH_BEARER"):
                fetch_artifacts.get_auth_bearer()
            env_file.write_text("AUTH_BEARER=first\nAUTH_BEARER=second\n")
            with self.assertRaisesRegex(ValueError, "Multiple AUTH_BEARER"):
                fetch_artifacts.get_auth_bearer()

    def test_prefers_compressed_and_supports_uncompressed(self):
        plain = self.raw / "123-genotype-rawdata.tsv"
        plain.write_text("plain")
        self.assertEqual(raw_data_path("123", self.raw), plain)
        self.write_raw("123")
        self.assertEqual(raw_data_path("123", self.raw).suffix, ".gz")
        self.assertEqual(find_barcodes(self.raw), ["123"])

    @unittest.skipUnless(os.environ.get("GTX_COMPARE_TEST_BARCODE"),
                         "GTX_COMPARE_TEST_BARCODE not set")
    def test_real_example_compressed_input(self):
        barcode = os.environ["GTX_COMPARE_TEST_BARCODE"]
        fetch_artifacts.validate_barcode(barcode)
        with gzip.open(raw_data_path(barcode), "rt", encoding="utf-8-sig", newline="") as file:
            sample = next(csv.DictReader(file, delimiter="\t"))
        marker, gt, category = sample["cpr"], sample["GT"], sample["globalkey"]
        genotype = self.artifacts / "genotype-json" / f"{barcode}.json"
        vital = self.artifacts / "vital-json" / f"{barcode}.json"
        genotype.parent.mkdir(parents=True)
        vital.parent.mkdir(parents=True)
        genotype.write_text(json.dumps({"content": {"genotype": {marker: gt}}}))
        vital.write_text(json.dumps({"content": {category: {
            "level": sample["level"], "score": sample["level_score"]}}}))
        genotype_path = compare_genotype.compare_barcode(barcode, RAW_DATA_DIR, self.artifacts)
        category_path = compare_category_level_score.compare_barcode(barcode, RAW_DATA_DIR, self.artifacts)
        reverse_path = compare_ep_categories_in_gtxplorer.compare_barcode(barcode, RAW_DATA_DIR, self.artifacts)
        workbook_path = run_comparison.create_workbook(barcode, genotype_path, category_path, reverse_path)
        workbook = load_workbook(workbook_path, read_only=True)
        self.addCleanup(workbook.close)
        merged_path = run_comparison.update_mismatched_workbook(barcode, genotype_path, category_path, reverse_path)
        merged = load_workbook(merged_path, read_only=True)
        self.addCleanup(merged.close)
        self.assertLessEqual(merged["genotype_comparison"].max_row,
                             workbook["genotype_comparison"].max_row)
        self.assertLessEqual(merged["category_comparison"].max_row,
                             workbook["category_comparison"].max_row)
        for sheet_name, path in (("genotype_comparison", genotype_path),
                                 ("category_comparison", category_path),
                                 ("ep_cateogory_comparison", reverse_path)):
            with path.open(newline="") as file:
                self.assertEqual(workbook[sheet_name].max_row,
                                 sum(1 for _ in csv.reader(file, delimiter="\t")))
        with (self.output / f"{barcode}-gtxplorer_vs_ep.tsv").open() as file:
            rows = csv.reader(file, delimiter="\t")
            next(rows)
            next(rows)
            self.assertEqual(next(rows)[1:5], [marker, gt, marker, gt])
        with (self.output / f"{barcode}-ep_vs_gtxplorer_category.tsv").open() as file:
            rows = list(csv.reader(file, delimiter="\t"))
        self.assertEqual(rows[2][2], category)
        self.assertEqual(rows[2][-1], "found_in_gtxplorer")


if __name__ == "__main__":
    unittest.main()

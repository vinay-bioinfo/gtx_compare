# gtXplorer and EP Comparison

This pipeline downloads Enterprise Portal (EP) JSON artifacts for each barcode,
compares them with gtXplorer raw data, and creates TSV and Excel reports. It
supports standard and UK artifact types and processes one or more barcodes.

## Requirements and setup

- Python 3.10 or later on Linux, and access to the EP API.
- A valid EP bearer token for the selected production or testing environment.
- A gtXplorer raw data file for each barcode.

From the project directory, install the Python dependency:

```bash
python3 -m pip install -r requirements.txt
```

Create a `.env` file in the project directory (use your own token, without a
`Bearer ` prefix):

```text
AUTH_BEARER=your_token_here
```

The `.env` file is git-ignored; do not commit or share the token. A nonempty
`AUTH_BEARER` environment variable takes precedence over `.env`. The wrapper and
standalone fetcher load the project `.env` regardless of the current directory.
An HTTP 401 may indicate an expired or incorrect token, or a token for the
wrong environment.

`fetcher_config.json` selects production by default. Set `use_production` to
`false` for testing, and configure the desired `artifact_types` if necessary;
`genotype-json` is required. The defaults include both UK and standard vital
and population-health artifacts. The fetcher downloads configured types
available on the latest non-terminated test; it reports unavailable types and
does not download VCFs or personal profiles.

By default, raw data is read from:

```text
/home/wangqian/projs/clinx-gtxplorer-related/gtxplorer_pgx/output/<BARCODE>-genotype-rawdata.tsv.gz
```

An uncompressed `<BARCODE>-genotype-rawdata.tsv` is also supported. If both
exist, the compressed file takes precedence.

## Run the complete pipeline

Replace the example numeric barcodes with your own:

```bash
# One barcode
python3 run_comparison.py --barcode 12345678901234

# Multiple barcodes
python3 run_comparison.py --barcode 12345678901234 12345678901235

# Barcodes from a file
python3 run_comparison.py --barcode-file barcodes.txt
```

The barcode file contains one numeric barcode per line, optionally preceded by
a `barcode` header:

```text
barcode
12345678901234
12345678901235
```

Blank lines are ignored and duplicates are processed once. The wrapper fetches
artifacts, runs all three comparisons, creates the per-barcode workbook, and
updates the merged mismatch workbook. It continues after a barcode fails and
exits nonzero if any barcode fails. Previous outputs are not removed on
failure; check the exit status before using them.

Override input locations if needed:

```bash
python3 run_comparison.py --barcode 12345678901234 \
  --raw-data-dir /path/to/raw-data \
  --artifacts-dir /path/to/artifacts \
  --config /path/to/fetcher_config.json
```

`--artifacts-dir` changes the artifact location only; comparison reports remain
under the project's `results/comparison/` directory. The wrapper uses only
artifacts fetched during the current run, so older artifacts do not affect its
comparisons.

## Run individual steps

These commands use the default input and output locations:

```bash
# Fetch configured artifacts for one barcode
python3 fetch_artifacts.py --barcode 12345678901234

# Run each comparison for that barcode
python3 compare_genotype.py --barcode 12345678901234
python3 compare_category_level_score.py --barcode 12345678901234
python3 compare_ep_categories_in_gtxplorer.py --barcode 12345678901234
```

The fetcher also accepts `--config PATH` and `--artifacts-dir PATH`. Each
comparison accepts `--raw-data-dir PATH` and `--artifacts-dir PATH`. To compare
all barcodes found in the raw data directory, use `--all` instead of `--barcode`:

```bash
python3 compare_genotype.py --all --raw-data-dir /path/to/raw-data
python3 compare_category_level_score.py --all --raw-data-dir /path/to/raw-data
python3 compare_ep_categories_in_gtxplorer.py --all --raw-data-dir /path/to/raw-data
```

Individual comparisons do not fetch artifacts or create Excel workbooks. They
use all available matching artifacts in the chosen artifacts directory, which
may include artifacts from previous runs.

## Output files

Results and barcode input files may contain sensitive sample data; keep them
private. The `results/` directory, `.env`, and `barcodes.txt` are git-ignored.

### Downloaded EP artifacts

`results/artifacts/<artifact-type>/<BARCODE>.json` contains the downloaded JSON
for one artifact type and barcode. `genotype-json` supplies the EP genotype and
missing-marker lists; category comparisons use entries with a `level` in the
other downloaded artifact types, including UK artifacts.

### Comparison TSVs

All three files are in `results/comparison/`. Each has two header rows: a source
grouping row followed by column names.

#### `<BARCODE>-gtxplorer_vs_ep.tsv`

One row per gtXplorer raw-data row. `marker_gtxplorer` and `gt_gtxplorer` come
from its `cpr` and `GT` columns. The script looks up the marker in EP
`genotype-json` and writes `marker_ep`, `gt_ep`, and `remarks/source`:

- `genotype-json-genotype`: marker is present in EP's genotype dictionary;
  `gt_ep` is its EP genotype.
- `genotype-json-missing`: marker is in EP's missing list; `gt_ep` is `.`.
- `genotype-json-not_found`: marker is in neither list; EP fields are empty.

The remark identifies where the marker was found, **not** whether the two
genotypes are equal.

#### `<BARCODE>-gtxplorer_vs_ep_category.tsv`

One row per unique gtXplorer `globalkey`, with the gtXplorer category display
name, level, and score alongside the EP category key, level, score, and
`ep_source_file`. The first gtXplorer row for a key supplies its displayed
category, level, and score. If several EP artifacts contain the key, the first
candidate with overlapping markers is preferred; otherwise the first candidate
is used. `ep_source_file` is `not_found` and EP fields are empty when there is
no candidate. A found category does not necessarily mean its level or score
matches.

#### `<BARCODE>-ep_vs_gtxplorer_category.tsv`

The reverse check: one row per EP artifact and category key. It reports EP's
source, category, level, and score alongside the corresponding gtXplorer
values. `remarks/source` is `found_in_gtxplorer` when the key exists in
gtXplorer, or `not_found_in_gtxplorer` otherwise. This remark reports key
presence, not level or score equality.

### `<BARCODE>-comparison.xlsx`

The wrapper writes this workbook in `results/comparison/` with all rows from
the three TSVs, including both header rows:

| Sheet | Source TSV |
| --- | --- |
| `genotype_comparison` | `<BARCODE>-gtxplorer_vs_ep.tsv` |
| `category_comparison` | `<BARCODE>-gtxplorer_vs_ep_category.tsv` |
| `ep_cateogory_comparison` | `<BARCODE>-ep_vs_gtxplorer_category.tsv` |

Values are stored as text to preserve barcodes and avoid interpreting values
as formulas. The sheet name `ep_cateogory_comparison` uses the existing spelling.

### `mismatched_merged.xlsx`

`results/comparison/mismatched_merged.xlsx` has the same three sheets and
headers, but collects mismatch/missing rows across successfully processed
barcodes. A row is included if:

- Its last column is empty or indicates missing, not found, or mismatch.
- A compared marker, genotype, category, level, or score is empty; a genotype
  of `.` is also considered missing.
- The marker or genotype differs, or the category level or score differs.
  Genotypes and levels are compared as text; scores are compared numerically
  when possible.

Rerunning a barcode replaces its previous merged rows, including removing them
if there are no longer mismatches; other barcodes remain. Failed samples leave
their previous merged rows intact. The workbook is regenerated on updates, so
manual formatting is not retained. `mismatched_merged.xlsx.lock` is a
coordination file used while updating the workbook, not a report.

## Console summary

After each successful barcode, the wrapper prints the downloaded artifact types,
an artifact variant (`UK`, `standard`, `mixed UK and standard`, or
`undetermined`), and `total`, `matched`, and `mismatched/missing` row counts for
each comparison. The variant reflects downloaded vital and population artifact
names, not an independently verified sample classification. The mismatch
counts use the same filter as the merged workbook. The individual comparison
scripts also print source-lookup counts, which measure different things.

## Tests

Run the local tests without fetching EP data:

```bash
python3 -m unittest -v test_pipeline
```

To additionally exercise an accessible real compressed raw-data file, set
`GTX_COMPARE_TEST_BARCODE` to its barcode before running the tests. The tests
use mock EP responses and temporary output files.

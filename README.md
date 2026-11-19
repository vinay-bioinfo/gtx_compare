# gtXplorer and EP Comparison

Fetch EP JSON artifacts and compare them with gtXplorer genotype and category data.
Python 3.10 or later is required. Install the workbook dependency:

```bash
python3 -m pip install -r requirements.txt
```

## Setup

Create a `.env` file in the project directory containing your Enterprise Portal
token (do not commit it):

```text
AUTH_BEARER=your_token_here
```

The `.env` file is git-ignored. An `AUTH_BEARER` environment variable, if set
and nonempty, takes precedence over `.env`. Both the wrapper and standalone
fetcher read this file regardless of the current working directory.

`fetcher_config.json` is based on clinical-sample-fetcher's config:
it selects production by default, specifies the endpoints and artifact types,
and includes the required `genotype-json`. Set `use_production` to `false` for
testing. Only JSON artifacts are downloaded; no VCFs or personal profiles are fetched.
Available configured artifacts from the latest non-terminated test are downloaded;
unavailable types are reported. A missing genotype artifact, download error, or
absence of category-level data fails that barcode.
The default configuration includes both `uk-vital-json` and
`uk-population-health-disease-risk-neuropsychiatric-json`, as well as their
standard counterparts; every configured type present on the latest test is
downloaded and included in the category comparisons.

Raw data is read directly from
`/home/wangqian/projs/clinx-gtxplorer-related/gtxplorer_pgx/output/` as
`<BARCODE>-genotype-rawdata.tsv.gz`. An uncompressed `.tsv` is also supported;
if both exist, `.tsv.gz` takes precedence. Override the directory with
`--raw-data-dir PATH`.

## Run the pipeline

```bash
python3 run_comparison.py --barcode 12345678901234
python3 run_comparison.py --barcode 12345678901234 12345678901235
python3 run_comparison.py --barcode-file barcodes.txt
```

The barcode file contains one numeric barcode per line, optionally preceded by
a `barcode` header. Duplicates are processed once. The pipeline continues to
other barcodes after a failure and exits nonzero if any barcode fails. Previous
outputs are not removed on failure; check the exit status before using them.
Use `--config PATH` or `--artifacts-dir PATH` to override the defaults.

Artifacts are saved to `results/artifacts/<artifact-type>/<BARCODE>.json`.
The pipeline compares only artifacts fetched in the current run, so older
artifacts for a barcode do not affect its results.
After each successful barcode, the wrapper prints the downloaded artifact types,
an artifact variant (UK, standard, mixed, or undetermined), and total, matched,
and mismatched/missing row counts for each comparison. The variant reflects
only the downloaded vital and population artifact names, not an independently
verified sample classification. The mismatch counts use the same filter as
the merged workbook; individual comparison scripts also report their own
source-lookup counts, which measure different things.

## Individual commands

```bash
python3 fetch_artifacts.py --barcode 12345678901234
python3 compare_genotype.py --barcode 12345678901234
python3 compare_category_level_score.py --barcode 12345678901234
python3 compare_ep_categories_in_gtxplorer.py --barcode 12345678901234
```

Each comparison accepts `--raw-data-dir`, `--artifacts-dir`, and `--all`
(discover all raw data files in the chosen directory). Individual comparisons
use all available matching artifacts in the chosen artifacts directory; unlike
the wrapper, they do not track which artifacts were fetched in the latest run.

## Outputs

Comparison TSV files are written to `results/comparison/`:

- `<BARCODE>-gtxplorer_vs_ep.tsv`: marker-level genotype comparison.
- `<BARCODE>-gtxplorer_vs_ep_category.tsv`: gtXplorer-to-EP category comparison.
- `<BARCODE>-ep_vs_gtxplorer_category.tsv`: EP-to-gtXplorer category comparison.

The wrapper also creates `<BARCODE>-comparison.xlsx` in the same directory,
with sheets `genotype_comparison`, `category_comparison`, and
`ep_cateogory_comparison`, respectively. TSV values are stored as text to
preserve barcodes and avoid interpreting values as formulas. Individual
comparison commands only produce TSVs.

The wrapper also maintains `results/comparison/mismatched_merged.xlsx` with the
same three sheets and headers. It includes rows whose last column is empty or
indicates missing/not found/mismatch, rows with missing comparison values, and
rows whose genotype, level, or score differs. Genotypes and levels are compared
as text; scores are compared numerically when possible. Each successful sample
updates the workbook, replacing any previous rows for that barcode while
retaining other samples. Failed samples leave their previous merged rows intact.
The workbook is regenerated on updates, so manual formatting is not retained.

# gtXplorer and EP Comparison Scripts

This project compares gtXplorer genotype/category output against EP JSON artifacts for the same barcode.

## Files

- `compare_genotype.py`: Matches gtXplorer CPRA markers against EP `genotype-json` values and missing-marker lists.
- `compare_category_level_score.py`: Matches gtXplorer category-level names, levels, and scores against EP category JSON artifacts.
- `compare_ep_categories_in_gtxplorer.py`: Performs the reverse category check, starting from EP artifacts and checking for matches in gtXplorer.

## Input layout

Place gtXplorer genotype TSV files in the project root using this naming pattern:

```text
<BARCODE>-genotype-rawdata.tsv
```

EP artifacts must be available under:

```text
results/artifacts/<artifact-type>/<BARCODE>.json
```

## Run the scripts

Run Python 3 from the project directory.

### Compare one barcode

```bash
python3 compare_genotype.py --barcode 27117164714547
python3 compare_category_level_score.py --barcode 27117164714547
python3 compare_ep_categories_in_gtxplorer.py --barcode 27117164714547
```

### Process every available barcode

```bash
python3 compare_genotype.py --all
python3 compare_category_level_score.py --all
python3 compare_ep_categories_in_gtxplorer.py --all
```

## Outputs

Comparison TSV files are written to `results/comparison/`:

- `<BARCODE>-gtxplorer_vs_ep.tsv`: Marker-level genotype comparison.
- `<BARCODE>-gtxplorer_vs_ep_category.tsv`: gtXplorer-to-EP category, level, and score comparison.
- `<BARCODE>-ep_vs_gtxplorer_category.tsv`: EP-to-gtXplorer reverse category comparison.

All comparison files use a tab-separated format.

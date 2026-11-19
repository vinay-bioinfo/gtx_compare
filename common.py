import gzip
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
RAW_DATA_DIR = Path("/home/wangqian/projs/clinx-gtxplorer-related/gtxplorer_pgx/output")
ARTIFACTS_DIR = BASE_DIR / "results" / "artifacts"
OUTPUT_DIR = BASE_DIR / "results" / "comparison"
RAW_SUFFIXES = ("-genotype-rawdata.tsv.gz", "-genotype-rawdata.tsv")


def find_barcodes(raw_data_dir: Path = RAW_DATA_DIR) -> list[str]:
    return sorted({
        path.name[:-len(suffix)]
        for suffix in RAW_SUFFIXES
        for path in raw_data_dir.glob(f"*{suffix}")
    })


def raw_data_path(barcode: str, raw_data_dir: Path = RAW_DATA_DIR) -> Path:
    for suffix in RAW_SUFFIXES:
        path = raw_data_dir / f"{barcode}{suffix}"
        if path.is_file():
            return path
    raise FileNotFoundError(f"Raw data TSV not found for {barcode} in {raw_data_dir}")


def open_raw_data(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8-sig", newline="")
    return path.open(encoding="utf-8-sig", newline="")

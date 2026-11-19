#!/usr/bin/env python3
"""Download the configured JSON artifacts from the latest non-terminated test."""

import argparse
import json
import os
import re
import shlex
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from common import ARTIFACTS_DIR, BASE_DIR

DEFAULT_CONFIG = BASE_DIR / "fetcher_config.json"
ENV_FILE = BASE_DIR / ".env"


def get_auth_bearer() -> str:
    token = os.environ.get("AUTH_BEARER")
    if token:
        return token
    try:
        with ENV_FILE.open(encoding="utf-8-sig") as file:
            lines = file.readlines()
    except FileNotFoundError:
        lines = []
    values = []
    for line in lines:
        match = re.match(r"^\s*(?:export\s+)?AUTH_BEARER\s*=(.*)$", line)
        if match:
            try:
                parts = shlex.split(match.group(1), comments=True)
            except ValueError:
                raise ValueError(f"Invalid AUTH_BEARER value in {ENV_FILE}") from None
            if len(parts) != 1:
                raise ValueError(f"Invalid AUTH_BEARER value in {ENV_FILE}")
            values.append(parts[0])
    if len(values) > 1:
        raise ValueError(f"Multiple AUTH_BEARER entries in {ENV_FILE}")
    if not values or not values[0]:
        raise ValueError(f"Set AUTH_BEARER in the environment or {ENV_FILE}")
    return values[0]


def validate_barcode(barcode: str) -> str:
    if not re.fullmatch(r"[0-9]+", barcode):
        raise ValueError(f"Invalid barcode {barcode!r}: expected digits only")
    return barcode


def read_json(url: str, headers: dict) -> object:
    with urlopen(Request(url, headers=headers), timeout=60) as response:
        return json.load(response)


def fetch_artifacts(barcode: str, config_path: Path = DEFAULT_CONFIG,
                    artifacts_dir: Path = ARTIFACTS_DIR) -> set[str]:
    validate_barcode(barcode)
    token = get_auth_bearer()
    with config_path.open(encoding="utf-8") as file:
        config = json.load(file)
    artifact_types = config["artifact_types"]
    if (not isinstance(artifact_types, list) or not artifact_types
            or any(not isinstance(name, str) or not re.fullmatch(r"[a-z0-9-]+", name)
                   for name in artifact_types)
            or "genotype-json" not in artifact_types):
        raise ValueError("artifact_types must be a list of safe artifact names including genotype-json")
    environment = "production" if config["use_production"] else "testing"
    urls = config["urls"][environment]
    headers = {
        "x-owner": "*",
        "authorization": f"Bearer {token}",
        "user-agent": config["user_agent"],
        "accept": "application/json",
        "origin": f"https://{urls['domain']}",
        "referer": f"https://{urls['domain']}/",
    }
    metadata = read_json(
        f"https://{urls['front']}/kit/v1.0/barcode/{barcode}?verbose=true&showall=true",
        headers,
    )
    if not isinstance(metadata, dict) or not isinstance(metadata.get("test"), list):
        raise ValueError(f"Invalid kit response for {barcode}: missing test list")
    tests = [test for test in metadata["test"]
             if isinstance(test, dict) and test.get("status") != "test-terminated"]
    if not tests:
        raise ValueError(f"No non-terminated test found for {barcode}")
    test = max(tests, key=lambda item: item.get("datetime") or "")
    if not isinstance(test.get("artifacts"), list) or not metadata.get("kitId"):
        raise ValueError(f"Invalid kit response for {barcode}: missing kit ID or artifacts")
    available = {}
    for entry in test["artifacts"]:
        if isinstance(entry, dict) and isinstance(entry.get("artifact"), dict):
            name, artifact_id = entry["artifact"].get("name"), entry.get("artifactId")
            if name in artifact_types and artifact_id:
                available[name] = str(artifact_id)

    missing = set(artifact_types) - available.keys()
    if "genotype-json" in missing:
        raise ValueError(f"genotype-json artifact not available for {barcode}")
    if len(available) < 2:
        raise ValueError(f"No non-genotype artifacts available for {barcode}")
    if missing:
        print(f"[{barcode}] unavailable artifacts: {', '.join(sorted(missing))}", file=sys.stderr)

    downloaded = {}
    for name in artifact_types:
        if name not in available:
            continue
        url = (f"https://{urls['back']}/test/v1.0/kit/kit/"
               f"{quote(str(metadata['kitId']), safe='')}/artifact/{quote(available[name], safe='')}")
        data = read_json(url, headers)
        if not isinstance(data, dict) or not isinstance(data.get("content"), dict):
            raise ValueError(f"Invalid {name} artifact for {barcode}: missing content object")
        downloaded[name] = data

    for name, data in downloaded.items():
        path = artifacts_dir / name / f"{barcode}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False)
            file.write("\n")
        print(f"[{barcode}] wrote {path}")
    return set(downloaded)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--barcode", required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--artifacts-dir", type=Path, default=ARTIFACTS_DIR)
    args = parser.parse_args()
    try:
        fetch_artifacts(args.barcode, args.config, args.artifacts_dir)
    except (ValueError, KeyError, OSError, URLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

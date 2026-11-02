#!/usr/bin/env python3
"""Stage CDSL input for MEDS-Extract and finish its code metadata.

The downloaded files are never modified.
"""

import argparse
import logging
import os
import shutil
import sys
from pathlib import Path

from CDSL_MEDS.code_descriptions import ICD10_DICTIONARY, apply_code_metadata

logger = logging.getLogger(__name__)

LATIN1_FILES = {"atc7.csv", "medication_05.csv", "vital_signs_04.csv"}


def stage(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise NotADirectoryError(source)
    if destination.exists():
        raise FileExistsError(f"Destination already exists: {destination}")

    destination.mkdir()
    for path in source.iterdir():
        if not path.is_file() or path.name.startswith("."):
            continue
        target = destination / path.name
        if path.name in LATIN1_FILES:
            with (
                path.open(encoding="latin-1", newline="") as src,
                target.open("w", encoding="utf-8", newline="") as dst,
            ):
                shutil.copyfileobj(src, dst)
        else:
            os.link(path, target)


def option(name: str) -> tuple[int, Path] | None:
    prefix = f"{name}="
    for index, arg in enumerate(sys.argv[1:], 1):
        if arg.startswith(prefix):
            return index, Path(arg.removeprefix(prefix))
    return None


def describe_codes(output_dir: Path, input_dir: Path | None) -> None:
    """Fill the descriptions MEDS-Extract cannot attach into the finished ``codes.parquet``."""
    codes_fp = output_dir / "metadata" / "codes.parquet"
    if not codes_fp.is_file():
        logger.warning(f"{codes_fp} not found; code metadata was not filled.")
        return
    candidates = [input_dir / ICD10_DICTIONARY] if input_dir else []
    candidates.append(output_dir / ".meds_extract_run" / "pre_meds" / ICD10_DICTIONARY)
    dictionary = next((fp for fp in candidates if fp.is_file()), None)
    counts = apply_code_metadata(codes_fp, dictionary)
    logger.info(f"Filled {counts['descriptions']} descriptions.")


def run() -> None:
    """Stage CDSL's Latin-1 files, run MEDS-Extract, then fill the remaining code descriptions."""
    input_dir = option("input_dir")
    output_dir = option("output_dir")
    if input_dir and output_dir and {path.name for path in input_dir[1].iterdir()} >= LATIN1_FILES:
        destination = output_dir[1] / ".meds_extract_run" / "pre_meds"
        destination.parent.mkdir(parents=True, exist_ok=True)
        stage(input_dir[1], destination)
        sys.argv[input_dir[0]] = f"input_dir={destination}"

    from MEDS_extract.run.cli import main as meds_extract_run

    try:
        meds_extract_run()
    except SystemExit as stop:  # The CLI always exits; stop here only if it failed.
        if stop.code:
            raise
    if output_dir:
        describe_codes(output_dir[1], input_dir[1] if input_dir else None)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create UTF-8 CDSL input for MEDS-Extract.")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    stage(args.source, args.destination)


if __name__ == "__main__":
    main()

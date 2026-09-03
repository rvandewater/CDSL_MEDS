#!/usr/bin/env python3
"""Stage CDSL input without modifying the downloaded files."""

import argparse
import os
import shutil
import sys
from pathlib import Path

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


def run() -> None:
    """Stage CDSL's Latin-1 files before delegating to MEDS-Extract."""
    input_dir = option("input_dir")
    output_dir = option("output_dir")
    if input_dir and output_dir and {path.name for path in input_dir[1].iterdir()} >= LATIN1_FILES:
        destination = output_dir[1] / ".meds_extract_run" / "pre_meds"
        destination.parent.mkdir(parents=True, exist_ok=True)
        stage(input_dir[1], destination)
        sys.argv[input_dir[0]] = f"input_dir={destination}"

    from MEDS_extract.run.cli import main as meds_extract_run

    meds_extract_run()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create UTF-8 CDSL input for MEDS-Extract.")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    stage(args.source, args.destination)


if __name__ == "__main__":
    main()

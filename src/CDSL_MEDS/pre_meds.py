"""``MEDS_extract-CDSL``: run MEDS-Extract on CDSL, finish its code metadata, then enrich it with suMEDS.

    MEDS_extract-CDSL spec=CDSL output_dir=DIR [meds-extract-run overrides ...] [--no-enrich]
        [-- suMEDS-enrich options ...]

1. ``meds-extract-run`` runs with every argument before a ``--``, except ``--no-enrich``.
2. The descriptions that MEDS-Extract ``_metadata`` blocks cannot attach (literal codes, and the
   slot-coded ICD codes of the wide tables) are filled into ``metadata/codes.parquet``; see
   :mod:`CDSL_MEDS.code_descriptions`.
3. Unless ``--no-enrich`` is given, ``suMEDS-enrich`` enriches that file in place with the rules
   registered as ``CDSL`` and every argument after the ``--``. suMEDS only fills what is still
   missing, so the dataset's own descriptions always win.

The raw files are never modified.
"""

import logging
import sys
from pathlib import Path

import sumeds
from sumeds.cli import build_enrich_parser, enrich_main

from CDSL_MEDS.code_descriptions import ICD10_DICTIONARY, apply_code_metadata

logger = logging.getLogger(__name__)

RULES = "CDSL"  # The name pyproject.toml registers sumeds_rules.yaml under.
NO_ENRICH = "--no-enrich"


def option(args: list[str], name: str) -> str | None:
    """The value of a ``name=value`` override in ``args``, else ``None``."""
    prefix = f"{name}="
    return next((arg.removeprefix(prefix) for arg in args if arg.startswith(prefix)), None)


def describe_codes(output_dir: Path, raw_dir: Path) -> None:
    """Fill the descriptions MEDS-Extract cannot attach into the finished ``codes.parquet``."""
    codes_fp = output_dir / "metadata" / "codes.parquet"
    if not codes_fp.is_file():
        logger.warning(f"{codes_fp} not found; code metadata was not filled.")
        return
    counts = apply_code_metadata(codes_fp, raw_dir / ICD10_DICTIONARY)
    logger.info(f"Filled {counts['descriptions']} descriptions.")


def run() -> int:
    """Run MEDS-Extract, fill the remaining code descriptions, then enrich them with suMEDS."""
    args = sys.argv[1:]
    cut = args.index("--") if "--" in args else len(args)
    overrides, enrich_args = [arg for arg in args[:cut] if arg != NO_ENRICH], args[cut + 1 :]
    output = option(overrides, "output_dir")
    codes = Path(output or ".").resolve() / "metadata" / "codes.parquet"
    enrich_argv = [str(codes), "-o", str(codes), "--rules", RULES, *enrich_args]
    enrich = NO_ENRICH not in args[:cut]
    if enrich:  # Bad suMEDS options or rules fail now, not after the pipeline.
        build_enrich_parser().parse_args(enrich_argv)
        sumeds.load_rules(RULES)

    from MEDS_extract.run.cli import main as meds_extract_run

    sys.argv[1:] = overrides
    try:
        meds_extract_run()
    except SystemExit as stop:  # The CLI always exits; stop here only if it failed.
        if stop.code:
            raise
    if output is None:  # e.g. --help
        return 0

    # The raw release: input_dir, or where meds-extract-run downloaded it.
    raw_dir = option(overrides, "input_dir") or option(overrides, "download_dest_dir")
    output_dir = Path(output)
    describe_codes(output_dir, Path(raw_dir) if raw_dir else output_dir / ".meds_extract_run" / "raw_input")
    return enrich_main(enrich_argv) if enrich else 0

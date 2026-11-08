"""A minimal synthetic CDSL release, built from the columns the config needs, and the ETL run over it."""

import csv
import os
import subprocess
import sys
from pathlib import Path

import pytest
from MEDS_extract.config import MessyConfig

ROOT = Path(__file__).parents[1]
CONFIG_FP = ROOT / "src/CDSL_MEDS/event_configs.yaml"

# Public example codes for the vocabulary-coded columns, so the fixture exercises their descriptions.
VOCABULARY_VALUES = {
    "dia_": "I10",
    "proc_": "5A1955Z",
    "neo_": "8140/3",
    "id_atc5": "H03AA",
    "id_atc7": "H03AA01",
}
# CDSL releases these tables in Latin-1 (`etl.encoding` in the config), so the fixture writes them
# that way, with non-ASCII text that only decodes correctly as Latin-1.
LATIN1_TABLES = {"atc7", "medication_05", "vital_signs_04"}
ATC7_NAME = "LEVOTIROXINA SÓDICA"
COMMERCIAL_NAME = "EUTIRÓX 100 µg"


def value_for(column):
    if value := next((v for prefix, v in VOCABULARY_VALUES.items() if column.startswith(prefix)), None):
        return value
    if column == "drug_comercial_name":
        return COMMERCIAL_NAME
    if column.endswith("_id"):
        return "1"
    if column == "time_constant_first_emerg":
        return "12:00"
    if "time" in column.lower():
        return "12:00:00"
    if column == "admission_d_inpat" or "date" in column.lower():
        return "2020-01-02 00:00:00"
    if column in {"age", "daily_avrg_dose"} or column.endswith(("_ing", "_emerg")):
        return "1"
    if column == "mechvent":
        return "Yes"
    return "X"


def run_etl(
    output: Path, raw: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    """Run ``meds-extract-run spec=CDSL`` over ``raw`` into ``output``, with extra ``args`` and ``env``."""
    return subprocess.run(
        [
            str(Path(sys.executable).parent / "meds-extract-run"),
            "spec=CDSL",
            f"output_dir={output}",
            "do_download=false",
            f"input_dir={raw}",
            *args,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=None if env is None else {**os.environ, **env},
    )


@pytest.fixture(scope="session")
def raw_dir(tmp_path_factory) -> Path:
    config = MessyConfig.load(CONFIG_FP)
    raw = tmp_path_factory.mktemp("cdsl_raw")
    for prefix, columns in config.needed_source_columns().items():
        encoding = "latin-1" if prefix in LATIN1_TABLES else "utf-8"
        with (raw / f"{prefix}.csv").open("w", newline="", encoding=encoding) as file:
            writer = csv.DictWriter(file, fieldnames=columns)
            writer.writeheader()
            writer.writerows(
                {
                    column: str(subject_id) if column.endswith("_id") else value_for(column)
                    for column in columns
                }
                for subject_id in range(1, 11)
            )
    (raw / "atc7.csv").write_bytes(f"id_atc7,atc7_name\nH03AA01,{ATC7_NAME}\n".encode("latin-1"))
    (raw / "icd10_codes_dict.txt").write_text(
        "11096|I10|1|Essential (primary) hypertension|Essential (primary) hypertension\n"
    )
    return raw


@pytest.fixture(scope="session")
def plain_output(tmp_path_factory, raw_dir: Path) -> Path:
    """The ETL without the suMEDS enrichment stage (``skip_stages=[sumeds_enrich]``)."""
    output = tmp_path_factory.mktemp("cdsl_plain") / "meds"
    result = run_etl(output, raw_dir, "skip_stages=[sumeds_enrich]")
    assert result.returncode == 0, result.stderr[-2000:]
    return output

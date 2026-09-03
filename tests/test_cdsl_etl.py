import csv
import os
import subprocess
import sys
from pathlib import Path

import yaml
from MEDS_extract.config import MessyConfig

from CDSL_MEDS import pre_meds

ROOT = Path(__file__).parents[1]


def test_config_loads_and_excludes_images():
    config = MessyConfig.load("./src/CDSL_MEDS/event_configs.yaml")
    needed = config.needed_source_columns()

    assert {"patient_01", "lab_06", "medication_05", "vital_signs_04"} <= set(needed)
    assert {"lab_id", "medication_id", "vital_sign_id"} <= set().union(*needed.values())
    raw = yaml.safe_load((ROOT / "src/CDSL_MEDS/event_configs.yaml").read_text())
    assert raw["sources"]["dataset"][0]["exclude"] == ["IMAGES/**"]


def value_for(column):
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


def test_etl_runs_on_a_minimal_valid_fixture(tmp_path):
    config = MessyConfig.load("./src/CDSL_MEDS/event_configs.yaml")
    raw = tmp_path / "raw"
    raw.mkdir()
    for prefix, columns in config.needed_source_columns().items():
        with (raw / f"{prefix}.csv").open("w", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=columns)
            writer.writeheader()
            writer.writerows(
                {
                    column: str(subject_id) if column.endswith("_id") else value_for(column)
                    for column in columns
                }
                for subject_id in range(1, 11)
            )
    (raw / "atc7.csv").write_text("id_atc7,atc7_name\nX,Example drug\n")

    output = tmp_path / "meds"
    result = subprocess.run(
        [
            str(Path(sys.executable).parent / "meds-extract-run"),
            f"spec={ROOT / 'src/CDSL_MEDS/event_configs.yaml'}",
            f"output_dir={output}",
            "do_download=false",
            f"input_dir={raw}",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr[-1000:]
    assert (output / ".meds_extract_run/pre_meds/atc7.csv").is_file()
    assert (output / "metadata/dataset.json").is_file()
    assert list((output / "data").glob("**/*.parquet"))


def test_preparer_transcodes_latin1_and_links_other_files(tmp_path):
    source = tmp_path / "raw"
    source.mkdir()
    (source / "atc7.csv").write_bytes(b"id_atc7,atc7_name\nH03AA01,LEVOTIROXINA S\xd3DICA\n")
    (source / "medication_05.csv").write_bytes(b"medication_id\n1\n")
    (source / "vital_signs_04.csv").write_bytes(b"vital_sign_id\n1\n")
    (source / "patient_01.csv").write_text("patient_id\n1\n")

    destination = tmp_path / "prepared"
    pre_meds.stage(source, destination)

    assert "SÓDICA" in (destination / "atc7.csv").read_text()
    assert os.path.samefile(source / "patient_01.csv", destination / "patient_01.csv")

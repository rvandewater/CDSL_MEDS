import json

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import yaml
from conftest import ATC7_NAME, COMMERCIAL_NAME, CONFIG_FP, DEATH_SUBJECT, DISCHARGE_DATE, ROOT
from meds import CodeMetadataSchema, DatasetMetadataSchema, death_code
from MEDS_extract.config import MessyConfig
from sumeds.unit_siblings import link_unit_siblings


def test_config_loads_and_excludes_images():
    config = MessyConfig.load("./src/CDSL_MEDS/event_configs.yaml")
    needed = config.needed_source_columns()

    assert {"patient_01", "lab_06", "medication_05", "vital_signs_04"} <= set(needed)
    assert {"lab_id", "medication_id", "vital_sign_id"} <= set().union(*needed.values())
    raw = yaml.safe_load((ROOT / "src/CDSL_MEDS/event_configs.yaml").read_text())
    assert raw["sources"]["dataset"][0]["exclude"] == ["IMAGES/**"]


def test_etl_runs_on_a_minimal_valid_fixture(plain_output):
    """``skip_stages=[sumeds_enrich]``: the ETL and its own descriptions, without suMEDS."""
    output = plain_output
    assert (output / "metadata/dataset.json").is_file()
    assert list((output / "data").glob("**/*.parquet"))

    # Every code but the ICD-10-PCS procedures (titled by suMEDS) is described, the ETL writes no
    # vocabulary parent codes (suMEDS adds them), and the file is valid MEDS.
    codes = pq.read_table(output / "metadata/codes.parquet")
    CodeMetadataSchema.validate(codes)
    metadata = {r["code"]: r for r in codes.to_pylist()}
    undescribed = {c for c, r in metadata.items() if not r["description"]}
    assert undescribed == {"PROCEDURE//ICD//10PCS//5A1955Z"}
    # The only parents are unit-free stems (`sumeds_link_unit_siblings`: `LAB//<item>` for
    # `LAB//<item>//<unit>`), in-dataset codes rather than vocabulary codes.
    assert all(
        r["parent_codes"] == [c.rsplit("//", 1)[0]] for c, r in metadata.items() if r.get("parent_codes")
    )
    assert metadata["DIAGNOSIS//ICD//10CM//I10"]["description"] == "Essential (primary) hypertension"
    assert metadata["NEOPLASM//ICDO//3//8140/3"]["description"].startswith("Neoplasm, ICD-O-3 morphology")
    assert metadata["MEDICATION//START//ATC5//H03AA//ATC7//H03AA01"]["description"] == ATC7_NAME
    assert metadata["MEDICATION//END//ATC5//H03AA//ATC7//H03AA01"]["description"].startswith(
        f"End of medication: {ATC7_NAME}"
    )


def test_lab_codes_carry_their_unit(plain_output):
    """A lab code's unit (`lab_06.ud_result`, the fixture's "X") is also code metadata, one String per code;
    the vital signs, whose unit the release does not document, carry none.

    suMEDS's `sumeds_normalize_units` writes `unit` in UCUM, keeps the spelling as recorded in
    `unit_source_value` and keeps a spelling it does not know, like "X", as it is.
    """
    codes = pq.read_table(plain_output / "metadata/codes.parquet")
    assert codes.schema.field("unit").type == pa.large_string()  # as suMEDS writes it back
    metadata = {r["code"]: r for r in codes.to_pylist()}
    assert metadata["LAB//X//X"]["unit"] == "X"
    assert metadata["LAB//X//X"]["unit_source_value"] == "X"
    assert not metadata["VITAL//HEART_RATE"]["unit"]
    assert not metadata["AGE_AT_ADMISSION"]["unit"]


def test_padded_and_blank_lab_units(plain_output):
    """The fixture's subject 2 records its lab unit as " X " and subject 3 (item "Y") a blank one: the first
    is `LAB//X//X` like every other subject's, and the second `LAB//Y//UNK`."""
    events = pl.concat([pl.read_parquet(fp) for fp in (plain_output / "data").rglob("*.parquet")])
    labs = events.filter(pl.col("code").str.starts_with("LAB//"))
    counts = dict(labs.group_by("code").len().iter_rows())
    assert counts == {"LAB//X//X": 9, "LAB//Y//UNK": 1}
    assert not events.filter(pl.col("code").str.contains(r"(\s|//)$")).height
    codes = pl.read_parquet(plain_output / "metadata/codes.parquet")
    metadata = {r["code"]: r for r in codes.to_dicts()}
    assert metadata["LAB//Y//UNK"]["unit"] is None
    assert metadata["LAB//Y//UNK"]["description"] == (
        "Laboratory test: Y; unit: UNK (CDSL lab_06.item_lab, lab_06.ud_result)"
    )
    assert metadata["LAB//X//X"]["description"] == (
        "Laboratory test: X; unit: X (CDSL lab_06.item_lab, lab_06.ud_result)"
    )


def test_lab_results_without_a_unit_join_their_item():
    """A lab result without a unit is `LAB//<item>//UNK`, as in the other MEDS ETLs, so suMEDS's
    `sumeds_link_unit_siblings`, with this config's options and its default `unknown_unit_components` ([UNK]),
    links it to its item's stem.

    Other `//UNKNOWN` codes (no unit) are left as they are.
    """
    raw = yaml.safe_load(CONFIG_FP.read_text())
    lab = raw["lab_06"]["laboratory_result"]
    assert lab["code"] == 'f"LAB//{$item_lab}//{$_value_unit}"'
    assert "{$_value_unit}" in lab["_metadata"]["_self"]["description"]
    assert raw["lab_06"]["_table"]["cols"]["_value_unit"].endswith('?? "UNK"')
    (options,) = [
        stage["sumeds_link_unit_siblings"]
        for stage in raw["etl"]["post_stages"]
        if isinstance(stage, dict) and "sumeds_link_unit_siblings" in stage
    ]
    assert "unknown_unit_components" not in options

    def description(unit):
        return f"Laboratory test: G; unit: {unit} (CDSL lab_06.item_lab, lab_06.ud_result)"

    codes = pl.DataFrame(
        {
            "code": ["LAB//G//mg/dL", "LAB//G//UNK", "HOSPITAL_DISCHARGE//UNKNOWN"],
            "description": [description("mg/dL"), description("UNK"), "Hospital discharge"],
            "unit": ["mg/dL", None, None],
            "parent_codes": [None, None, None],
        },
        schema_overrides={"parent_codes": pl.List(pl.String)},
    )
    out, counts = link_unit_siblings(codes, **options)
    rows = {r["code"]: r for r in out.to_dicts()}
    assert rows["LAB//G//mg/dL"]["parent_codes"] == ["LAB//G"]
    assert rows["LAB//G//UNK"]["parent_codes"] == ["LAB//G"]
    assert rows["LAB//G//UNK"]["unit"] is None
    assert rows["LAB//G"]["description"] == "Laboratory test: G (CDSL lab_06.item_lab, lab_06.ud_result)"
    assert rows["HOSPITAL_DISCHARGE//UNKNOWN"]["parent_codes"] is None
    assert (counts["sibling_groups"], counts["sibling_codes"]) == (1, 2)


def test_coded_diagnoses_and_procedures_fall_at_discharge(plain_output):
    """`diagnosis_er_02` and `diagnosis_hosp_03` have no time; their codes are placed at the stay's hospital
    discharge (`patient_01.discharge_date`), never before admission (they were 1970-01-01)."""
    events = pl.concat([pl.read_parquet(fp) for fp in (plain_output / "data").rglob("*.parquet")])
    admission = (
        events.filter(pl.col("code") == "HOSPITAL_ADMISSION")
        .group_by("subject_id")
        .agg(pl.col("time").min().alias("admission"))
    )
    discharge = (
        events.filter(pl.col("code").str.starts_with("HOSPITAL_DISCHARGE//"))
        .group_by("subject_id")
        .agg(pl.col("time").max().alias("discharge"))
    )
    events = events.join(admission, on="subject_id").join(discharge, on="subject_id")
    assert events.height and events["time"].null_count() == 0

    assert events.filter(pl.col("time") < pl.col("admission")).is_empty()

    coded = events.filter(
        pl.col("code").str.contains(r"^(DIAGNOSIS//ICD//10CM|PROCEDURE//ICD//10PCS|NEOPLASM//ICDO//3)//")
    )
    families = set(coded["code"].str.split("//").list.head(3).list.join("//"))
    assert families == {"DIAGNOSIS//ICD//10CM", "PROCEDURE//ICD//10PCS", "NEOPLASM//ICDO//3"}
    assert (coded["time"] == coded["discharge"]).all()
    assert set(coded["time"].dt.strftime("%Y-%m-%d %H:%M:%S")) == {DISCHARGE_DATE}


def test_latin1_sources_are_decoded(plain_output, raw_dir):
    """The fixture's Latin-1 tables are not UTF-8, yet their text arrives intact (`etl.encoding`)."""
    events = pl.concat([pl.read_parquet(fp) for fp in (plain_output / "data").rglob("*.parquet")])
    medications = events.filter(pl.col("code").str.starts_with("MEDICATION//"))
    assert medications.height and set(medications["text_value"]) == {COMMERCIAL_NAME}
    with pytest.raises(UnicodeDecodeError):
        (raw_dir / "atc7.csv").read_text(encoding="utf-8")


def test_dataset_json_describes_the_release_and_the_extra_columns(plain_output):
    """The `etl:` descriptors reach `metadata/dataset.json`, and every extra column is accounted for."""
    metadata = json.loads((plain_output / "metadata/dataset.json").read_text())
    DatasetMetadataSchema.validate(metadata)
    assert metadata["license"] == "PhysioNet Contributor Review Health Data License 1.5.0"
    assert metadata["description_uri"] == "https://physionet.org/content/covid-data-shared-learning/1.0.0/"
    assert metadata["raw_source_id_columns"] == [
        "lab_id",
        "medication_id",
        "vital_sign_id",
        "study_id",
        "image_id",
    ]
    events = pl.concat([pl.read_parquet(fp) for fp in (plain_output / "data").rglob("*.parquet")])
    core = {"subject_id", "time", "code", "numeric_value", "text_value"}
    listed = set(metadata["raw_source_id_columns"]) | set(metadata["other_extension_columns"])
    assert listed == set(events.columns) - core


def test_death_discharge_is_meds_death(plain_output):
    """A stay whose discharge destination is `Death` (the fixture's subject 1) keeps its
    `HOSPITAL_DISCHARGE//Death` event and also gets one `MEDS_DEATH` (`meds.death_code`) at the same time; no
    other subject gets one."""
    events = pl.concat([pl.read_parquet(fp) for fp in (plain_output / "data").rglob("*.parquet")])
    death = events.filter(pl.col("code") == death_code)
    discharge = events.filter(pl.col("code") == "HOSPITAL_DISCHARGE//Death")
    assert death.height == 1 and discharge.height == 1
    assert death["subject_id"].to_list() == discharge["subject_id"].to_list() == [DEATH_SUBJECT]
    assert death["time"].to_list() == discharge["time"].to_list()
    assert death["time"].dt.strftime("%Y-%m-%d %H:%M:%S").to_list() == [DISCHARGE_DATE]
    assert death.select(pl.col("numeric_value", "text_value").is_null().all()).row(0) == (True, True)
    # Every other subject is discharged elsewhere and has no death.
    others = events.filter(pl.col("subject_id") != DEATH_SUBJECT)
    assert others.filter(pl.col("code").str.starts_with("HOSPITAL_DISCHARGE//")).height == 9
    assert others.filter(pl.col("code") == death_code).is_empty()

    metadata = {r["code"]: r for r in pl.read_parquet(plain_output / "metadata/codes.parquet").to_dicts()}
    description = metadata[death_code]["description"]
    assert description.startswith("Death (MEDS standard code): in-hospital death")
    assert "patient_01.destin_discharge" in description and "no follow-up after discharge" in description

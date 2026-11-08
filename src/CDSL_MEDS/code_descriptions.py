"""The ``cdsl_describe_slot_codes`` stage: descriptions of the slot-coded diagnosis and neoplasm codes.

Most CDSL codes are described in ``event_configs.yaml``: the ``atc7`` dictionary join for
medications, ``_self`` templates for the codes built from one event's columns, and constant ``_self``
blocks for the literal codes (``HOSPITAL_ADMISSION``, ``VITAL//HEART_RATE``, ...).

The **slot-coded codes** (``DIAGNOSIS_ICD10//``, ``NEOPLASM_ICDO3//``) cannot be described there. They
come from one event per column of a wide table (``dia_ppal``, ``dia_02``, ...), so a code seen in two
columns has two code templates, which the MEDS-Extract metadata stage rejects; those events cannot
carry ``_metadata``. This module describes them in the finished ``metadata/codes.parquet``, as a
MEDS-Extract post-stage that the spec runs before ``sumeds_enrich``::

    etl:
      post_stages:
        - cdsl_describe_slot_codes
        - sumeds_enrich: {rules: CDSL}

Diagnosis descriptions come from the release's own ICD-10-CM dictionary, ``icd10_codes_dict.txt``
(read from the run's ``input_dir``); ICD-O-3 morphology codes get a template naming the code.

Vocabulary parent codes (ICD-10-CM, ICD-10-PCS, ICD-O-3, ATC), and the official titles of the
ICD-10-CM and ICD-10-PCS codes the release does not describe, are not added here: suMEDS adds them
with the rules in ``sumeds_rules.yaml``, in the ``sumeds_enrich`` stage that runs after this one.

Only missing values are filled: an extracted description always wins, no code is added, and running
the stage twice changes nothing.
"""

import logging
import os
import re
from collections.abc import Mapping
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
from meds import CodeMetadataSchema, code_metadata_filepath
from MEDS_transforms.stages import Stage

logger = logging.getLogger(__name__)

STAGE_NAME = "cdsl_describe_slot_codes"

# The release's ICD-10 dictionary ("Additional tables" on the PhysioNet page). It is the CMS
# ICD-10-CM FY2021 order file with its fixed-width fields joined by "|", without a header
# (theonesp/covidhm-code, CovidHM_V3-code/buildcovidhm/data_wrangling/V3_icd10_data_wrangling.py).
ICD10_DICTIONARY = "icd10_codes_dict.txt"
ICD10_DICTIONARY_FIELDS = (
    "order_number",
    "code",
    "valid_for_submission",
    "short_description",
    "long_description",
)

# Code shapes. CDSL writes ICD-10-CM codes with the dot after the category; the dictionary has them
# without it. ICD-O-3 morphology is MMMM/B, optionally followed by the 6th-digit grade or
# differentiation code; such codes also occur in the diagnosis columns.
ICD10CM_CODE = re.compile(r"^[A-Z][0-9][0-9A-Z](\.[0-9A-Z]{1,4})?$")
ICDO3_MORPHOLOGY_CODE = re.compile(r"^[0-9]{4}/[0-9]{1,2}$")

DIAGNOSIS_SOURCE = "CDSL diagnosis_er_02 / diagnosis_hosp_03 dia_* columns"
NEOPLASM_SOURCE = "CDSL diagnosis_hosp_03 neo_* columns"


def read_icd10_dictionary(path: Path) -> dict[str, str]:
    """Map undotted ICD-10-CM codes to their long titles from the release's dictionary.

    Each line holds the five order-file fields joined by ``|``. The long title is the last field
    and is split off with a bounded split, so a ``|`` inside it would survive.
    """
    titles = {}
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            fields = line.rstrip("\n").split("|", len(ICD10_DICTIONARY_FIELDS) - 1)
            if len(fields) != len(ICD10_DICTIONARY_FIELDS):
                continue
            row = dict(zip(ICD10_DICTIONARY_FIELDS, fields, strict=True))
            titles[row["code"].strip()] = row["long_description"].strip()
    return titles


def slot_code_description(code: str, icd10_titles: Mapping[str, str]) -> str | None:
    """Return the description of a slot-coded diagnosis or neoplasm code, else ``None``.

    ICD-10-CM codes are described only from the release's own dictionary. Codes it lacks, and all
    ``PROCEDURE_ICD10PCS`` codes, are left undescribed, so that suMEDS can fill their official
    titles from its versioned ICD-10-CM and ICD-10-PCS data (suMEDS fills only missing descriptions).

    Examples:
        >>> titles = {"I10": "Essential (primary) hypertension"}
        >>> slot_code_description("DIAGNOSIS_ICD10//I10", titles)
        'Essential (primary) hypertension'
        >>> slot_code_description("DIAGNOSIS_ICD10//J18.9", titles) is None  # not in the dictionary
        True
        >>> slot_code_description("DIAGNOSIS_ICD10//8140/3", titles).split(" (")[0]
        'ICD-O-3 morphology code 8140/3 in an ICD-10 diagnosis column'
        >>> slot_code_description("NEOPLASM_ICDO3//8140/32", titles)
        'Neoplasm, ICD-O-3 morphology code 8140/32 (CDSL diagnosis_hosp_03 neo_* columns)'
        >>> slot_code_description("PROCEDURE_ICD10PCS//5A1955Z", titles) is None
        True
        >>> slot_code_description("LAB//Glucose//mg/dL", titles) is None
        True
    """
    family, sep, value = code.partition("//")
    if not sep:
        return None
    if family == "DIAGNOSIS_ICD10":
        if ICD10CM_CODE.match(value):
            return icd10_titles.get(value.replace(".", ""))
        if ICDO3_MORPHOLOGY_CODE.match(value):
            return f"ICD-O-3 morphology code {value} in an ICD-10 diagnosis column ({DIAGNOSIS_SOURCE})"
        return f"Diagnosis code {value} ({DIAGNOSIS_SOURCE})"
    if family == "NEOPLASM_ICDO3":
        return f"Neoplasm, ICD-O-3 morphology code {value} ({NEOPLASM_SOURCE})"
    return None


def apply_code_metadata(
    codes_fp: Path, icd10_dictionary: Path | None = None, out_fp: Path | None = None
) -> dict[str, int]:
    """Fill the missing descriptions of slot-coded codes in a finished ``codes.parquet``.

    Args:
        codes_fp: The ``metadata/codes.parquet`` of a finished extraction.
        icd10_dictionary: The release's ``icd10_codes_dict.txt``. Without it, ICD-10-CM codes stay
            undescribed (suMEDS can fill their titles).
        out_fp: Where to write the result; by default ``codes_fp`` itself.

    Returns:
        The number of codes whose description was filled.

    The file is validated against the MEDS code-metadata schema and replaced atomically. Polars
    writes strings as Arrow ``large_string``, which that schema rejects, so the MEDS columns are
    cast back to the types the pipeline itself writes (``string``, ``list<string>``).
    """
    codes_fp = Path(codes_fp)
    out_fp = codes_fp if out_fp is None else Path(out_fp)
    titles = {}
    if icd10_dictionary is not None and Path(icd10_dictionary).is_file():
        titles = read_icd10_dictionary(icd10_dictionary)
    else:
        logger.warning(f"{icd10_dictionary or ICD10_DICTIONARY} not found; ICD-10-CM codes stay undescribed.")

    codes = pl.from_arrow(pq.read_table(codes_fp))
    if "description" not in codes.columns:
        codes = codes.with_columns(pl.lit(None, dtype=pl.String).alias("description"))

    filled = codes.with_columns(
        pl.coalesce(
            "description",
            pl.Series([slot_code_description(code, titles) for code in codes["code"]], dtype=pl.String),
        ).alias("description")
    )
    counts = {
        "descriptions": int(
            filled["description"].is_not_null().sum() - codes["description"].is_not_null().sum()
        ),
    }

    out = filled.to_arrow()
    meds_types = {"code": pa.string(), "description": pa.string(), "parent_codes": pa.list_(pa.string())}
    out = out.cast(pa.schema([pa.field(f.name, meds_types.get(f.name, f.type)) for f in out.schema]))
    CodeMetadataSchema.validate(out)

    tmp_fp = out_fp.with_name(f".{out_fp.name}.tmp")
    pq.write_table(out, tmp_fp)
    os.replace(tmp_fp, out_fp)
    return counts


@Stage.register(
    stage_name=STAGE_NAME, is_metadata=True, default_config={"icd10_dictionary": ICD10_DICTIONARY}
)
def main(cfg) -> None:
    """Describe the slot-coded codes of the finished cohort's ``metadata/codes.parquet``, in place.

    ``icd10_dictionary`` is the release's ICD-10-CM dictionary, relative to the run's ``input_dir``
    (or absolute). If it is missing, a warning is logged and ICD-10-CM codes stay undescribed.
    """
    if cfg.worker != 0:
        return
    codes = Path(code_metadata_filepath).name
    dictionary = Path(cfg.input_dir) / cfg.stage_cfg.icd10_dictionary
    counts = apply_code_metadata(
        Path(cfg.stage_cfg.metadata_input_dir) / codes,
        dictionary,
        out_fp=Path(cfg.stage_cfg.reducer_output_dir) / codes,
    )
    logger.info(f"Described {counts['descriptions']} slot-coded codes.")

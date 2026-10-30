"""Code metadata that MEDS-Extract cannot attach through ``_metadata`` blocks.

Most CDSL code families are described in ``event_configs.yaml``: the ``atc7`` dictionary join
for medications, and ``_self`` templates for the codes built from one event's columns. Two kinds
of codes cannot be described there, so ``MEDS_extract-CDSL`` fills them into the finished
``metadata/codes.parquet`` with :func:`apply_code_metadata`:

* **Literal codes** (``HOSPITAL_ADMISSION``, ``VITAL//HEART_RATE``, ...) have no code components
  for ``_metadata`` to join on. MEDS-Extract documents a pre-existing ``codes.parquet`` for this
  case, but the standard extraction pipeline never reads it. Their descriptions are in
  ``metadata/static_code_descriptions.csv``, with the source of each row.
* **Slot-coded codes** (``DIAGNOSIS_ICD10//``, ``NEOPLASM_ICDO3//``) come from one event per
  column of a wide table (``dia_ppal``, ``dia_02``, ...). A code seen in two columns then has two
  code templates, which the MEDS-Extract metadata reducer rejects, so those events cannot carry
  ``_metadata`` either. Diagnosis descriptions come from the release's own ICD-10-CM dictionary,
  ``icd10_codes_dict.txt``; ICD-O-3 morphology codes get a template naming the code.

Vocabulary parent codes (ICD-10-CM, ICD-10-PCS, ICD-O-3, ATC), and the official titles of the
ICD-10-CM and ICD-10-PCS codes the release does not describe, are not added here: suMEDS adds
them with the rules in ``sumeds_rules.yaml``.

Only missing values are filled: an extracted description always wins, no code is added, and
running the fill twice changes nothing.
"""

import logging
import os
import re
from collections.abc import Mapping
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
from meds import CodeMetadataSchema

logger = logging.getLogger(__name__)

METADATA_DIR = Path(__file__).parent / "metadata"
STATIC_DESCRIPTIONS_CSV = METADATA_DIR / "static_code_descriptions.csv"

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


def read_static_code_descriptions(path: Path = STATIC_DESCRIPTIONS_CSV) -> pl.DataFrame:
    """Read the packaged descriptions of literal codes as ``code``, ``description``.

    The ``source`` column documents the table itself and is not written into the dataset.
    """
    return pl.read_csv(path, infer_schema=False).select("code", "description")


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
    codes_fp: Path, icd10_dictionary: Path | None = None, static: pl.DataFrame | None = None
) -> dict[str, int]:
    """Fill missing descriptions in a finished ``codes.parquet``, in place.

    Args:
        codes_fp: The ``metadata/codes.parquet`` of a finished extraction.
        icd10_dictionary: The release's ``icd10_codes_dict.txt``. Without it, ICD-10-CM codes stay
            undescribed (suMEDS can fill their titles).
        static: Override of the packaged literal-code table (``code``, ``description``), for tests.

    Returns:
        The number of codes whose description was filled.

    The file is validated against the MEDS code-metadata schema and replaced atomically. Polars
    writes strings as Arrow ``large_string``, which that schema rejects, so the MEDS columns are
    cast back to the types the pipeline itself writes (``string``, ``list<string>``).
    """
    codes_fp = Path(codes_fp)
    static = read_static_code_descriptions() if static is None else static
    titles = {}
    if icd10_dictionary is not None and Path(icd10_dictionary).is_file():
        titles = read_icd10_dictionary(icd10_dictionary)
    else:
        logger.warning(f"{ICD10_DICTIONARY} not found; ICD-10-CM codes are left undescribed.")

    codes = pl.from_arrow(pq.read_table(codes_fp))
    if "description" not in codes.columns:
        codes = codes.with_columns(pl.lit(None, dtype=pl.String).alias("description"))

    filled = (
        codes.with_columns(
            pl.Series(
                "_slot_description",
                [slot_code_description(code, titles) for code in codes["code"]],
                dtype=pl.String,
            ),
        )
        .join(static.rename({"description": "_static"}), on="code", how="left", maintain_order="left")
        .with_columns(pl.coalesce("description", "_static", "_slot_description").alias("description"))
        .select(codes.columns)
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

    tmp_fp = codes_fp.with_suffix(".parquet.tmp")
    pq.write_table(out, tmp_fp)
    os.replace(tmp_fp, codes_fp)
    return counts

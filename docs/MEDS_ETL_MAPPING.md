# CDSL v1.0.0 to MEDS mapping

This ETL uses the CDSL files in a directory containing `patient_01.csv`. It does not
read, download, or represent files from `IMAGES/`.

## Identity and time

`patient_id` is the CDSL admission identifier and is the MEDS `subject_id`. CDSL documents
one admission per identifier, so it is also retained as `inpatient_stay_id` where the
source has an admission-level event. Dates are deidentified but preserve within-patient
chronology; direct source datetimes are emitted as supplied.

The diagnosis, procedure, and neoplasm tables provide no event timestamp. Their events
use the explicit shared pseudotime `1970-01-01 00:00:00`, rather than guessing admission
or discharge time.

## Tables

| Source                          | MEDS events                                                                                                                | Time                                                          | Notes                                                                                                                                                                                                     |
| ------------------------------- | -------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `patient_01.csv`                | Hospital/ICU/ED visits, demographics, inpatient and ED diagnoses, mechanical ventilation, first/last ED vital measurements | Documented admission, discharge, ICU, ED, or measurement time | Missing times drop their event.                                                                                                                                                                           |
| `diagnosis_er_02.csv`           | ICD-10 diagnosis slots and ICD-10-PCS procedure slots                                                                      | Pseudotime                                                    | Every populated wide slot is emitted.                                                                                                                                                                     |
| `diagnosis_hosp_03.csv`         | ICD-10 diagnosis slots, ICD-10-PCS procedure slots, ICD-O-3 neoplasm slots                                                 | Pseudotime                                                    | Every populated wide slot is emitted; POA flags have no MEDS mapping.                                                                                                                                     |
| `vital_signs_04.csv`            | Systolic/diastolic pressure, temperature, heart rate, oxygen saturation, glucose                                           | `constants_ing_date` + `constants_ing_time`                   | `vital_sign_id` prevents loss of repeated identical measurements. Values, including zero, are retained because the release documentation does not define a missing-value sentinel. No units are asserted. |
| `medication_05.csv`             | Medication start and end                                                                                                   | `drug_start_date`, `drug_end_date`                            | `medication_id` preserves source-row identity. Code preserves ATC5 and ATC7 identifiers; commercial name is text. `daily_avrg_dose` is numeric without an invented unit.                                  |
| `atc7.csv`                      | Medication code metadata                                                                                                   | N/A                                                           | Supplies the documented ATC7 description.                                                                                                                                                                 |
| `lab_06.csv`                    | Laboratory results                                                                                                         | `lab_date` + `time_lab`                                       | `lab_id` prevents de-duplication of otherwise equal results. Code includes source item and unit (or `UNKNOWN`); numeric and textual results plus reference range are retained.                            |
| `CDSL-1.0.0-dicom-metadata.csv` | Imaging metadata events                                                                                                    | `StudyDate` + `StudyTime`                                     | Preserves study/image identifiers and supplied modality metadata; it does not require image files.                                                                                                        |

## Input preparation and validation

`atc7.csv`, `medication_05.csv`, and `vital_signs_04.csv` contain Latin-1 bytes.
`MEDS_extract-CDSL` invokes `CDSL_MEDS.pre_meds` first, creating a UTF-8 staging view
without modifying the downloaded files.

Validate both the configuration and emitted artifacts:

```bash
uv run pytest
uv run MEDS_extract-CDSL spec=CDSL output_dir=cdsl_meds \
	do_download=false input_dir=raw_cdsl/raw_cdsl
```

A successful run produces `cdsl_meds/data/**/*.parquet` and
`cdsl_meds/metadata/{dataset.json,codes.parquet,subject_splits.parquet}`.

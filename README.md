# CDSL MEDS ETL

Configuration-first [MEDS-Extract](https://github.com/mmcdermott/MEDS_extract) ETL for
[COVID Data for Shared Learning (CDSL) v1.0.0](https://physionet.org/content/covid-data-shared-learning/1.0.0/).
It maps the tabular EHR data and DICOM metadata, never the `IMAGES/` tree.

## Download raw data without images

After PhysioNet grants your data-use agreement:

```bash
read -rp "PhysioNet username: " USERNAME
read -srp "PhysioNet password: " PASSWORD
echo
mkdir -p raw_cdsl && cd raw_cdsl &&
	wget -r -N -c -np -nH --cut-dirs=3 \
		--user="$USERNAME" --password="$PASSWORD" \
		--reject-regex='.*/IMAGES/.*' \
		https://physionet.org/files/covid-data-shared-learning/1.0.0/
unset USERNAME PASSWORD
```

Or let MEDS-Extract download the same source declaratively:

```bash
export DATASET_DOWNLOAD_USERNAME="$PHYSIONET_USERNAME"
export DATASET_DOWNLOAD_PASSWORD="$PHYSIONET_PASSWORD"
uv run meds-extract-download spec=CDSL output_dir=raw_cdsl
```

## Run

Three CDSL files are Latin-1 encoded, while MEDS-Extract's CSV reader requires UTF-8.
The installed `meds-extract-run` stages a UTF-8 view automatically, without changing
the downloaded files:

```bash
uv run meds-extract-run spec=CDSL output_dir=cdsl_meds \
	do_download=false input_dir=raw_cdsl/raw_cdsl
```

Set `input_dir` to the directory that directly contains `patient_01.csv` if your
download layout differs. The internal staging step hard-links every other source file
and transcodes only `atc7.csv`, `medication_05.csv`, and `vital_signs_04.csv`.

## Mapping

CDSL's dates are deidentified but preserve within-patient chronology and are emitted
directly. Diagnosis, procedure, and neoplasm tables supply no timestamps, so those events
use the explicit `1970-01-01` pseudotime anchor. See
[docs/MEDS_ETL_MAPPING.md](docs/MEDS_ETL_MAPPING.md) for the table-level mapping and
limitations.

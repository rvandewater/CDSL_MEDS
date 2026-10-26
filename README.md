# CDSL MEDS ETL

<!-- Placeholders until available: the PyPI badges resolve once CDSL-MEDS is published; replace
CODECOV_TOKEN_PLACEHOLDER with the repo's Codecov badge token and zenodo.XXXXXXX with its Zenodo DOI. -->

[![PyPI - Version](https://img.shields.io/pypi/v/CDSL-MEDS)](https://pypi.org/project/CDSL-MEDS/)
[![codecov](https://codecov.io/gh/rvandewater/CDSL_MEDS/graph/badge.svg?token=CODECOV_TOKEN_PLACEHOLDER)](https://codecov.io/gh/rvandewater/CDSL_MEDS)
[![tests](https://github.com/rvandewater/CDSL_MEDS/actions/workflows/synthetic-etl.yaml/badge.svg)](https://github.com/rvandewater/CDSL_MEDS/actions/workflows/synthetic-etl.yaml)
[![code-quality](https://github.com/rvandewater/CDSL_MEDS/actions/workflows/code-quality-main.yaml/badge.svg)](https://github.com/rvandewater/CDSL_MEDS/actions/workflows/code-quality-main.yaml)
![python](https://img.shields.io/badge/python-%3E%3D3.11.4%2C%20%3C3.14-blue?logo=python&logoColor=white)
![Static Badge](https://img.shields.io/badge/MEDS-0.4.1-blue)
[![license](https://img.shields.io/badge/License-MIT-green.svg?labelColor=gray)](https://github.com/rvandewater/CDSL_MEDS#license)
[![PRs](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/rvandewater/CDSL_MEDS/pulls)
[![contributors](https://img.shields.io/github/contributors/rvandewater/CDSL_MEDS.svg)](https://github.com/rvandewater/CDSL_MEDS/graphs/contributors)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.XXXXXXX.svg)](https://doi.org/10.5281/zenodo.XXXXXXX)

Configuration-first [MEDS-Extract](https://github.com/mmcdermott/MEDS_extract) ETL for
[COVID Data for Shared Learning (CDSL) v1.0.0](https://physionet.org/content/covid-data-shared-learning/1.0.0/).
It maps the tabular EHR data and DICOM metadata, never the `IMAGES/` tree.

## Run

Three CDSL files are Latin-1 encoded, while MEDS-Extract's CSV reader requires UTF-8.
The `MEDS_extract-CDSL` command stages a UTF-8 view automatically, without changing
the downloaded files, and then runs `meds-extract-run`. It takes the same arguments:

```bash
uv run MEDS_extract-CDSL spec=CDSL output_dir=cdsl_meds \
	do_download=false input_dir=raw_cdsl
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

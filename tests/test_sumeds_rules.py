"""The packaged suMEDS rules parse, and their patterns match the code layouts of the ETL config.

The example codes are built from the code templates in ``event_configs.yaml`` with synthetic
(public example) values, so a change to a code layout that the rules no longer match fails here.
"""

import re
from importlib.resources import files
from pathlib import Path

import yaml

PACKAGE_DIR = Path(__file__).parents[1] / "src" / "CDSL_MEDS"
RULES_FP = PACKAGE_DIR / "sumeds_rules.yaml"
CONFIG_FP = PACKAGE_DIR / "event_configs.yaml"

RULE_KINDS = {"schema", "ontology", "crosswalk", "hierarchy"}
ONTOLOGY_KEYS = {"id", "pattern", "vocabulary", "normalize", "validate"}
HIERARCHY_KEYS = {"id", "pattern", "group", "separator", "mode", "ignore", "nearest_only"}
FROM_ONTOLOGY_KEYS = {"id", "from_ontology", "rules", "nearest_only"}
VALIDATE_MODES = {"pack", "athena", "none"}

# Synthetic values for the config's columns, keyed by column name or `prefix*`.
DIAGNOSES = ["I10", "J18.9", "U07.1", "S72.001A", "8140/3", "8000/31"]
PROCEDURES = ["5A1955Z", "0BH17EZ"]
NEOPLASMS = ["8140/3", "8140/32"]
MEDICATIONS = [  # (id_atc5, id_atc7)
    ("H03AA", "H03AA01"),  # ATC7 under its ATC5 code
    ("N02BE", None),  # no ATC7 code: rendered as UNKNOWN
    ("J01CR", "J01CA04"),  # ATC7 not under the row's ATC5 code
]

# The ontology rule (first in file order) that each example code must resolve through.
EXPECTED_ONTOLOGY = {
    "DIAGNOSIS_ICD10//I10": "cdsl-icd10cm-diagnoses",
    "DIAGNOSIS_ICD10//J18.9": "cdsl-icd10cm-diagnoses",
    "DIAGNOSIS_ICD10//U07.1": "cdsl-icd10cm-diagnoses",
    "DIAGNOSIS_ICD10//S72.001A": "cdsl-icd10cm-diagnoses",
    "DIAGNOSIS_ICD10//8140/3": "cdsl-icdo3-diagnoses",
    "DIAGNOSIS_ICD10//8000/31": "cdsl-icdo3-diagnoses",
    "PROCEDURE_ICD10PCS//5A1955Z": "cdsl-icd10pcs-procedures",
    "PROCEDURE_ICD10PCS//0BH17EZ": "cdsl-icd10pcs-procedures",
    "NEOPLASM_ICDO3//8140/3": "cdsl-icdo3-neoplasms",
    "NEOPLASM_ICDO3//8140/32": "cdsl-icdo3-neoplasms",
    "MEDICATION//ATC5//H03AA//ATC7//H03AA01": "cdsl-atc7-medications",
    "MEDICATION_END//ATC5//H03AA//ATC7//H03AA01": "cdsl-atc7-medications",
    "MEDICATION//ATC5//N02BE//ATC7//UNKNOWN": "cdsl-atc5-medications",
    "MEDICATION_END//ATC5//N02BE//ATC7//UNKNOWN": "cdsl-atc5-medications",
    "MEDICATION//ATC5//J01CR//ATC7//J01CA04": "cdsl-atc5-medications",
    "MEDICATION_END//ATC5//J01CR//ATC7//J01CA04": "cdsl-atc5-medications",
}
# The vocabulary code each example resolves to (the `code` group).
EXPECTED_VOCABULARY_CODE = {
    "DIAGNOSIS_ICD10//S72.001A": "S72.001A",
    "DIAGNOSIS_ICD10//8000/31": "8000/3",
    "NEOPLASM_ICDO3//8140/32": "8140/3",
    "MEDICATION//ATC5//H03AA//ATC7//H03AA01": "H03AA01",
    "MEDICATION//ATC5//N02BE//ATC7//UNKNOWN": "N02BE",
    "MEDICATION//ATC5//J01CR//ATC7//J01CA04": "J01CR",
}
# Hierarchy rule id -> example codes it must match, with the value of its `group`.
EXPECTED_HIERARCHY = {
    "cdsl-diagnosis-prefixes": {"DIAGNOSIS_ICD10//J18.9": "J18.9", "DIAGNOSIS_ICD10//I10": "I10"},
    "cdsl-procedure-prefixes": {"PROCEDURE_ICD10PCS//5A1955Z": "5A1955Z"},
}


def load_rules() -> dict:
    return yaml.safe_load(RULES_FP.read_text())


def code_templates() -> list[str]:
    cfg = yaml.safe_load(CONFIG_FP.read_text())
    templates = []
    for table, events in cfg.items():
        if table in ("_defaults", "etl", "sources"):
            continue
        templates += [str(e["code"]) for name, e in events.items() if not name.startswith("_")]
    return templates


def render(template: str, values: dict[str, str | None]) -> str:
    """Render a config code expression: a literal, or an f-string of column fields with defaults."""
    head = template.split(" if ")[0].strip()
    if not head.startswith('f"'):
        return head.strip('"')

    def value(match: re.Match) -> str:
        column, default = match.group(1), match.group(2)
        got = values.get(column, next((v for k, v in values.items() if column.startswith(k)), "X"))
        return default if got is None else got

    return re.sub(r"\{\$(\w+)(?: \?\? '([^']*)')?\}", value, head[2:-1])


def example_codes() -> set[str]:
    """Every code template of the config, rendered with each synthetic value set."""
    value_sets = [
        {"dia_": d, "proc_": p, "neo_": n}
        for d, p, n in zip(DIAGNOSES, PROCEDURES * 3, NEOPLASMS * 3, strict=True)
    ]
    value_sets += [{"id_atc5": atc5, "id_atc7": atc7} for atc5, atc7 in MEDICATIONS]
    return {render(t, values) for t in code_templates() for values in value_sets}


def test_rules_file_is_packaged():
    assert "*.yaml" in Path(__file__).parents[1].joinpath("pyproject.toml").read_text()
    assert files("CDSL_MEDS").joinpath("sumeds_rules.yaml").is_file()


def test_rules_file_parses_and_is_well_formed():
    rules = load_rules()
    assert set(rules) <= RULE_KINDS
    assert rules["schema"] == "sumeds-rules/0.1"
    assert not rules.get("crosswalk")
    ids = [r["id"] for kind in ("ontology", "crosswalk", "hierarchy") for r in rules.get(kind) or []]
    assert len(ids) == len(set(ids)), "duplicate rule ids"

    ontology_ids = set()
    for rule in rules["ontology"]:
        assert set(rule) <= ONTOLOGY_KEYS and {"id", "pattern", "vocabulary"} <= set(rule), rule["id"]
        assert "code" in re.compile(rule["pattern"]).groupindex, rule["id"]
        assert rule["pattern"].startswith("^") and rule["pattern"].endswith("$"), rule["id"]
        assert rule.get("validate", "pack") in VALIDATE_MODES, rule["id"]
        assert rule["vocabulary"].strip() and "/" not in rule["vocabulary"], rule["id"]
        for step in rule.get("normalize") or []:
            assert set(step) == {"pattern", "replace"}
            re.compile(step["pattern"])
        ontology_ids.add(rule["id"])

    for rule in rules["hierarchy"]:
        if rule.get("from_ontology"):
            assert set(rule) <= FROM_ONTOLOGY_KEYS, rule["id"]
            assert set(rule.get("rules") or []) <= ontology_ids, rule["id"]
            continue
        assert set(rule) <= HIERARCHY_KEYS, rule["id"]
        pattern = re.compile(rule["pattern"])
        assert rule["pattern"].startswith("^") and rule["pattern"].endswith("$"), rule["id"]
        assert rule["group"] in pattern.groupindex, rule["id"]
        assert (rule.get("mode") == "prefix") != ("separator" in rule), rule["id"]
        assert isinstance(rule.get("nearest_only", True), bool), rule["id"]


def test_ontology_rules_match_the_config_code_layouts():
    rules = load_rules()["ontology"]
    compiled = [(r["id"], re.compile(r["pattern"])) for r in rules]
    examples = example_codes()
    assert set(EXPECTED_ONTOLOGY) <= examples, set(EXPECTED_ONTOLOGY) - examples

    resolved = {}
    for code in examples:
        hit = next(((rule_id, m) for rule_id, p in compiled if (m := p.search(code))), None)
        if hit:
            resolved[code] = hit
    assert {code: rule_id for code, (rule_id, _) in resolved.items()} == EXPECTED_ONTOLOGY
    for code, value in EXPECTED_VOCABULARY_CODE.items():
        assert resolved[code][1].group("code") == value, code
    # Every rule is exercised by at least one example.
    assert {rule_id for rule_id, _ in resolved.values()} == {r["id"] for r in rules}


def test_hierarchy_rules_match_the_config_code_layouts():
    rules = {r["id"]: r for r in load_rules()["hierarchy"] if not r.get("from_ontology")}
    assert set(rules) == set(EXPECTED_HIERARCHY)
    examples = example_codes()
    for rule_id, expected in EXPECTED_HIERARCHY.items():
        pattern = re.compile(rules[rule_id]["pattern"])
        matched = {}
        for code in examples:
            if m := pattern.search(code):
                matched[code] = m.group(rules[rule_id]["group"])
        assert expected.items() <= matched.items(), rule_id
        # Only codes of the rule's own family match.
        family = next(iter(expected)).split("//")[0]
        assert all(code.startswith(f"{family}//") for code in matched), rule_id

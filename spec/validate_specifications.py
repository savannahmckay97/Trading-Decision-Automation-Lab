from __future__ import annotations

from pathlib import Path
import sys
import yaml


ROOT = Path(__file__).resolve().parent
RULES_PATH = ROOT / "rule_catalog.yaml"
DATA_PATH = ROOT / "data_dictionary.yaml"


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = yaml.safe_load(handle)
    if not isinstance(value, dict):
        raise AssertionError(f"{path.name}: root must be a mapping")
    return value


def validate() -> list[str]:
    rules_doc = load_yaml(RULES_PATH)
    data_doc = load_yaml(DATA_PATH)
    rules = rules_doc.get("rules", [])
    fields = data_doc.get("fields", {})
    derived = data_doc.get("derived_fields", {})
    configuration = rules_doc.get("configuration_defaults", {})

    errors: list[str] = []
    warnings: list[str] = []

    if not rules:
        errors.append("rule_catalog.yaml contains no rules")

    ids = [rule.get("rule_id") for rule in rules]
    if None in ids:
        errors.append("Every rule must have rule_id")
    duplicates = sorted({rule_id for rule_id in ids if ids.count(rule_id) > 1})
    if duplicates:
        errors.append(f"Duplicate rule IDs: {duplicates}")

    permitted_actions = {
        "RISK_BLOCKED", "DATA_STALE", "annotate", "score", "emit_state"
    }
    known_inputs = set(fields) | set(derived)

    for rule in rules:
        rule_id = rule.get("rule_id", "<missing>")
        for required_key in ("version", "tier", "purpose", "inputs", "action", "independence_group"):
            if required_key not in rule:
                errors.append(f"{rule_id}: missing {required_key}")

        action = rule.get("action")
        if action not in permitted_actions:
            errors.append(f"{rule_id}: unknown action {action!r}")

        if rule.get("live_execution_allowed") is True:
            errors.append(f"{rule_id}: v0.1 may not allow live execution")

        for input_name in rule.get("inputs", []):
            if input_name not in known_inputs:
                errors.append(f"{rule_id}: undefined input {input_name}")

        for output_name in rule.get("output_fields", []):
            if output_name not in derived:
                errors.append(f"{rule_id}: undefined derived output {output_name}")
        output_name = rule.get("output_field")
        if output_name and output_name not in derived:
            errors.append(f"{rule_id}: undefined derived output {output_name}")

    for field_name, definition in fields.items():
        if "type" not in definition or "source" not in definition:
            errors.append(f"{field_name}: requires type and source")
        freshness = definition.get("freshness_ms")
        if freshness is not None and (not isinstance(freshness, int) or freshness <= 0):
            errors.append(f"{field_name}: freshness_ms must be positive integer or null")
        for rule_id in definition.get("required_by", []):
            if rule_id not in ids:
                errors.append(f"{field_name}: required_by unknown rule {rule_id}")

    for name, definition in derived.items():
        producer = definition.get("producer")
        if not producer:
            errors.append(f"{name}: derived field has no producer")
        elif producer not in ids and producer not in {"feature_engine"}:
            errors.append(f"{name}: unknown producer {producer}")

    config_fields = {
        name.removeprefix("config.")
        for name in fields
        if name.startswith("config.")
    }
    missing_config = sorted(config_fields - set(configuration))
    unused_config = sorted(set(configuration) - config_fields)
    if missing_config:
        errors.append(f"Configuration fields without defaults: {missing_config}")
    if unused_config:
        warnings.append(f"Defaults without dictionary fields: {unused_config}")

    setup = next((rule for rule in rules if rule.get("rule_id") == "SETUP_BREAKOUT_RETEST_V1"), None)
    if setup is None:
        errors.append("SETUP_BREAKOUT_RETEST_V1 is required")
    else:
        states = set(setup.get("state_machine", {}).get("states", []))
        initial = setup.get("state_machine", {}).get("initial")
        if initial not in states:
            errors.append("Breakout state machine initial state is not declared")
        for transition in setup.get("state_machine", {}).get("transitions", []):
            if transition.get("from") not in states or transition.get("to") not in states:
                errors.append(f"Invalid state transition: {transition}")

    if errors:
        raise AssertionError("\n".join(errors))
    return warnings


if __name__ == "__main__":
    try:
        warnings = validate()
    except Exception as exc:
        print(f"SPECIFICATION VALIDATION FAILED\n{exc}")
        sys.exit(1)
    print("SPECIFICATION VALIDATION PASSED")
    print(f"Rules: {len(load_yaml(RULES_PATH)['rules'])}")
    print(f"Raw/config fields: {len(load_yaml(DATA_PATH)['fields'])}")
    print(f"Derived fields: {len(load_yaml(DATA_PATH)['derived_fields'])}")
    for warning in warnings:
        print(f"WARNING: {warning}")

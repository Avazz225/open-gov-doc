"""The shipped monitoring-defaults package (P88-S7, ADR 0252) stays importable:
it parses as a `ConfigDocument`, every rule would pass monitoring-service's
validation, and names are unique (import upserts by name)."""

import json
from pathlib import Path

from config_service.schemas import ConfigDocument

PACKAGE = Path(__file__).resolve().parents[3] / "packages" / "monitoring-defaults" / "config.json"
AGGREGATIONS = {"max", "min", "sum", "avg", "rate_per_minute"}


def _load() -> ConfigDocument:
    return ConfigDocument.model_validate(json.loads(PACKAGE.read_text(encoding="utf-8")))


def test_package_parses_with_manifest_and_only_alerting_categories():
    doc = _load()
    assert doc.manifest is not None
    assert doc.manifest.name == "Monitoring-Standardpaket"
    assert doc.alert_rules
    populated = {
        name
        for name, value in doc.model_dump(exclude={"manifest", "exported_at"}).items()
        if value is not None and name != "schema_version"
    }
    assert populated == {"alert_rules", "alert_downtime_windows"}


def test_every_rule_would_pass_monitoring_validation():
    """Mirrors `monitoring_service.alerting.validate_rule` (no cross-service
    import, same convention as the export schemas)."""
    for rule in _load().alert_rules:
        thresholds = [
            v
            for v in (rule.min_error, rule.min_warning, rule.max_warning, rule.max_error)
            if v is not None
        ]
        assert thresholds, rule.name
        assert thresholds == sorted(thresholds), rule.name
        assert rule.aggregation in AGGREGATIONS, rule.name
        assert rule.warning_repeat >= 1 and rule.error_repeat >= 1, rule.name
        assert rule.description, rule.name


def test_names_are_unique_and_windows_name_existing_rules():
    doc = _load()
    names = [rule.name for rule in doc.alert_rules]
    assert len(names) == len(set(names))
    for window in doc.alert_downtime_windows or []:
        assert window.rule_names is None or set(window.rule_names) <= set(names)

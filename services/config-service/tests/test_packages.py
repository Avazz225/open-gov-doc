"""The shipped configuration packages stay importable (P88-S7, P89-S3): each
parses as a `ConfigDocument`; the monitoring-defaults rules would pass
monitoring-service's validation; the eGov package's four-eyes and stamping
defaults are what its README promises."""

import json
import string
from pathlib import Path

import pytest
from config_service.schemas import ConfigDocument

PACKAGES = Path(__file__).resolve().parents[3] / "packages"
PACKAGE = PACKAGES / "monitoring-defaults" / "config.json"
EGOV = PACKAGES / "egov" / "config.json"
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


@pytest.mark.parametrize(
    "path", sorted(PACKAGES.glob("*/config.json")), ids=lambda p: p.parent.name
)
def test_every_shipped_package_parses_with_a_manifest(path):
    doc = ConfigDocument.model_validate(json.loads(path.read_text(encoding="utf-8")))
    assert doc.manifest is not None and doc.manifest.version


def _egov() -> ConfigDocument:
    return ConfigDocument.model_validate(json.loads(EGOV.read_text(encoding="utf-8")))


def test_egov_four_eyes_covers_role_changes_and_ad_group_mapping():
    """P89-S3: Konzept 14.2 "permission change" - role content and the
    AD-group mapping decide who gets which rights, like role assignments."""
    gated = {c.action_type for c in _egov().approval_config if c.requires_approval}
    assert {
        "permission.role.create",
        "permission.role.update",
        "permission.role_assignment.create",
        "auth.ad_group_role_mapping.create",
        "auth.ad_group_role_mapping.delete",
        "auth.ad_group_role_composite_rule.create",
        "auth.ad_group_role_composite_rule.delete",
        "auth.ad_group_mapping.default_role_set",
    } <= gated


def test_egov_stamping_defaults_pass_document_service_validation():
    """Mirrors document-service's `update_export_config` checks: only the
    two known placeholders, and `diagonal-center` only for a text stamp."""
    config = _egov().export_config
    assert config is not None and config.stamp_enabled
    parsed = string.Formatter().parse(config.stamp_value_template)
    fields = {name for _, name, _, _ in parsed if name}
    assert fields <= {"kennzeichen", "document_id"}
    assert config.stamp_type == "text" or config.stamp_position != "diagonal-center"

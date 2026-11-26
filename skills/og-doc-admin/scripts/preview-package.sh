#!/usr/bin/env bash
# Shows what importing a configuration package (or a fragment) would change,
# per category, against the installation's live state - without changing
# anything. Usage: scripts/preview-package.sh <package.json>
set -euo pipefail
file="${1:?usage: preview-package.sh <package.json>}"
# Only the categories the file contains, so the summary isn't padded with
# untouched ones.
categories=$(python3 -c '
import json, sys
known = {"object_types","workflows","dmn_definitions","business_calendars","roles","approval_config",
         "sensor_config","federation_config","realm_roles","ad_group_mappings","branding_config",
         "deletion_reason_catalogs","org_units","org_unit_role_links","alert_rules",
         "alert_downtime_windows","export_config","structure_config","folder_templates"}
doc = json.load(open(sys.argv[1]))
print(" ".join(f"--category {c}" for c in doc if c in known))
' "$file")
if [ -z "$categories" ]; then
  echo "No known configuration category in $file." >&2
  exit 1
fi
# shellcheck disable=SC2086
dms config compare "$file" $categories
echo
echo "only_in_compare = new, differing = would change, identical = unchanged."
echo "only_in_base entries stay: an import never deletes. Details: dms -o json config compare $file"

# Branding and export stamp

**Example:** "Produktname und Akzentfarbe" / "Exportstempel 'Kopie' unten rechts"
**Role:** domain-admin-config, domain-admin-document-config

1. `dms -o json config export --category branding_config --category export_config`.
2. Change the fields in a fragment: `product_name`, `accent_color`, `logo_url`; `stamp_enabled`,
   `stamp_type`, `stamp_value_template`, `stamp_position`.
3. **Preview:** `scripts/preview-package.sh fragment.json`.
4. After a yes: `dms config import fragment.json`.
5. **Verify:** export again.

**Note:** An unknown placeholder in the stamp template is rejected with `422`; show the message.

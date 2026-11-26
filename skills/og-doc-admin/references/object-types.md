# Object types (document and folder types)

- `dms object-type list|get <id>|create -f <file>|update <id> -f <file>|delete <id>`. Role needed:
  `domain-admin-config`.
- **Fields:**
  - `name`, `applies_to` (`document`/`folder`);
  - `attributes` (each with `name`, `type`, `required`, and optional constraints);
  - `kennzeichen_format` (reference number);
  - `default_retention_days`, `default_archive_after_days`;
  - `classification_level`, `required_signature_level`;
  - filing rules (`allowed_parent_types`, `allowed_child_folder_types`, `allowed_document_types`,
    defaults);
  - `status_transitions`, `metadata_mappings`.
- **Update:** read the type (`dms -o json object-type get <id>`), change only what the admin asked for,
  show the diff, then `update`. `object_type.update` can be under four-eyes.
- **Delete** a type only if nothing uses it. It is irreversible, so ask.
- For several types at once, or to take them to another installation, use the `object_types` package
  category (`packages.md`).

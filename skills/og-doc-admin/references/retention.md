# Retention and trash

- `dms retention show` shows, for document-service and folder-service:
  - the trash restore period;
  - whether a deletion reason is required;
  - the reminder lead days;
  - the deletion-reason catalogue.
- `dms retention set [--trash-days N] [--reason-required/--no-reason-required] [--reminder-lead-days N]
  [--add-reason "…"]… [--remove-reason "…"]… [--align-reasons]` changes only what is given, on **both**
  services. Role needed: `domain-admin-retention`.
- **The two catalogues are separate on purpose** (documents and folders may differ, ADR 0233). `show`
  lists a difference below the table. If they differ, tell the admin and offer `--align-reasons`: both
  get the union, so nothing is lost. The admin UI („Aufbewahrung & Papierkorb") offers the same.
- **Retention periods per type** (`default_retention_days`, archive after) belong to the object type
  (`object-types.md`).
- **Deleting records,** force-delete, destruction and legal holds are not done by you. Point to the admin
  UI ("Löschregister", "Aussonderung").

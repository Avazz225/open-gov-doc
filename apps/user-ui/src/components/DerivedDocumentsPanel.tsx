"use client";

import { useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import { listDerivedDocuments, type DocumentSummary } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// First actual read-side consumer of the P6-S3 provenance fields
// (`derived_from_document_id`, previously write-only - post-roadmap phase
// 31 session 4, ADR 0115). Shown only when at least one derived copy
// exists, to avoid an empty section on every document. Plain `<a>` links
// (not an in-app tab-open callback) - a real navigation to `/?document=ID`
// is resolved by `DocumentWorkspace`'s existing direct-link mount effect
// (post-roadmap phase 29, ADR 0109), the simplest correct way to open an
// arbitrary document from a leaf panel that has no workspace context of
// its own.
export function DerivedDocumentsPanel({ document: activeDocument }: { document: DocumentSummary }) {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [derived, setDerived] = useState<DocumentSummary[]>([]);

  useEffect(() => {
    if (!accessToken) return;
    listDerivedDocuments(accessToken, activeDocument.id)
      .then(setDerived)
      .catch(() => setDerived([]));
  }, [accessToken, activeDocument.id]);

  if (derived.length === 0) return null;

  return (
    <section className="derived-documents-panel" aria-label={t("derivedDocuments.heading")}>
      <h2 className="pane-heading">{t("derivedDocuments.heading")}</h2>
      <ul>
        {derived.map((doc) => (
          <li key={doc.id}>
            <a href={`/?document=${encodeURIComponent(doc.id)}`}>{doc.title}</a>
            {doc.derivation_type === "redaction" && (
              <span className="badge classified">{t("derivedDocuments.redactionBadge")}</span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

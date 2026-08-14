"use client";

import { useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  createSignature,
  listDocumentVersions,
  listSignatures,
  verifySignature,
  type DocumentSummary,
  type SignatureLevel,
  type SignatureSummary,
  type SignatureVerification,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Signaturformat ist aktuell ausschließlich PAdES (3.10) - der
// Signature Service lehnt jede andere aktuelle Version serverseitig mit
// einem Fehler ab (`main.py`: `content_type != "application/pdf"`). Ohne
// diese Prüfung bot die UI das Signieren-Formular für jedes Dokument an,
// auch wenn ein Klick garantiert scheitern musste (Nutzer-Feedback).
const SIGNABLE_CONTENT_TYPE = "application/pdf";

// Anbau-Muster wie OCR-/Renditions-Anzeige in PreviewPane.tsx (eigener
// list*-Aufruf, eigener Lade-Effekt, non-blocking Fallback-UI) - unter das
// Metadaten-Formular gesetzt, da es sich um eine dokumentgebundene, aber
// nicht editierbare Zusatzinformation handelt (3.10, seit P6-S7). QES ist in
// der Niveau-Auswahl bewusst nicht wählbar - dieses Grundgerüst hat keinen
// konfigurierten externen QTSP-Connector (siehe docs/services/
// signature-service.md "Offene Punkte").
export function SignaturesPanel({
  document: activeDocument,
  onSigned,
}: {
  document: DocumentSummary;
  onSigned?: (documentId: string) => void;
}) {
  const { accessToken, user } = useAuth();
  const { t } = useI18n();
  const [signatures, setSignatures] = useState<SignatureSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [level, setLevel] = useState<SignatureLevel>("ses");
  const [isSigning, setIsSigning] = useState(false);
  const [verifications, setVerifications] = useState<Record<number, SignatureVerification>>({});
  const [verifyingId, setVerifyingId] = useState<number | null>(null);
  const [isSignable, setIsSignable] = useState(false);

  useEffect(() => {
    setError(null);
    setVerifications({});
    if (!accessToken) return;
    listSignatures(accessToken, activeDocument.id)
      .then(setSignatures)
      .catch(() => setError(t("signatures.loadError")));
  }, [accessToken, activeDocument.id, t]);

  // Aktuelle Version auf Signierbarkeit prüfen (nur PDF, siehe oben) - eigener
  // Aufruf statt eines Props-Durchreichens von `PreviewPane`, da beide
  // Komponenten unabhängig voneinander eingebunden werden.
  useEffect(() => {
    setIsSignable(false);
    if (!accessToken) return;
    let cancelled = false;
    listDocumentVersions(accessToken, activeDocument.id)
      .then((versions) => {
        if (cancelled) return;
        const current = versions.find(
          (v) => v.version_number === activeDocument.current_version_number
        );
        setIsSignable(current?.content_type === SIGNABLE_CONTENT_TYPE);
      })
      .catch(() => {
        if (!cancelled) setIsSignable(false);
      });
    return () => {
      cancelled = true;
    };
  }, [accessToken, activeDocument.id, activeDocument.current_version_number]);

  async function handleSign() {
    if (!accessToken || !user) return;
    setError(null);
    setIsSigning(true);
    try {
      const created = await createSignature(accessToken, {
        documentId: activeDocument.id,
        level,
        signerPrincipalId: user.username,
      });
      setSignatures((prev) => [created, ...prev]);
      // Die PAdES-Signatur erzeugt serverseitig eine neue Dokumentversion
      // (ADR 0025) - PreviewPane weiß davon nichts, da beide Panels
      // unabhängig voneinander gemountet sind (P23-S7).
      onSigned?.(activeDocument.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("signatures.signError"));
    } finally {
      setIsSigning(false);
    }
  }

  async function handleVerify(signatureId: number) {
    if (!accessToken) return;
    setError(null);
    setVerifyingId(signatureId);
    try {
      const result = await verifySignature(accessToken, signatureId);
      setVerifications((prev) => ({ ...prev, [signatureId]: result }));
    } catch {
      setError(t("signatures.verifyError"));
    } finally {
      setVerifyingId(null);
    }
  }

  return (
    <section className="signatures-panel" aria-label={t("signatures.heading")}>
      <h2 className="pane-heading">{t("signatures.heading")}</h2>

      {signatures.length === 0 ? (
        <p className="empty-state">{t("signatures.noSignatures")}</p>
      ) : (
        <ul className="signature-list">
          {signatures.map((signature) => {
            const verification = verifications[signature.id];
            return (
              <li key={signature.id}>
                <span className="signature-level">{signature.level.toUpperCase()}</span>
                <span>
                  {t("signatures.signedBy", {
                    name: signature.signer_display_name,
                    date: new Date(signature.signed_at).toLocaleString(),
                  })}
                </span>
                <button
                  type="button"
                  onClick={() => handleVerify(signature.id)}
                  disabled={verifyingId === signature.id}
                >
                  {verifyingId === signature.id
                    ? t("signatures.verifying")
                    : t("signatures.verifyButton")}
                </button>
                {verification && (
                  <span className={verification.valid ? "badge-valid" : "badge-invalid"}>
                    {verification.valid ? t("signatures.statusValid") : t("signatures.statusInvalid")}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {isSignable ? (
        <div className="signature-actions">
          <label>
            {t("signatures.levelLabel")}
            <select value={level} onChange={(e) => setLevel(e.target.value as SignatureLevel)}>
              <option value="ses">{t("signatures.levelSes")}</option>
              <option value="aes">{t("signatures.levelAes")}</option>
              <option value="qes" disabled>
                {t("signatures.levelQesUnavailable")}
              </option>
            </select>
          </label>
          <button type="button" onClick={handleSign} disabled={isSigning}>
            {isSigning ? t("signatures.signing") : t("signatures.signButton")}
          </button>
        </div>
      ) : (
        <p className="hint">{t("signatures.notSignable")}</p>
      )}

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

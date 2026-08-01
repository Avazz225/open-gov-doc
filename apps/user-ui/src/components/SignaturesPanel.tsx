"use client";

import { useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  createSignature,
  listSignatures,
  verifySignature,
  type DocumentSummary,
  type SignatureLevel,
  type SignatureSummary,
  type SignatureVerification,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Anbau-Muster wie OCR-/Renditions-Anzeige in PreviewPane.tsx (eigener
// list*-Aufruf, eigener Lade-Effekt, non-blocking Fallback-UI) - unter das
// Metadaten-Formular gesetzt, da es sich um eine dokumentgebundene, aber
// nicht editierbare Zusatzinformation handelt (3.10, seit P6-S7). QES ist in
// der Niveau-Auswahl bewusst nicht wählbar - dieses Grundgerüst hat keinen
// konfigurierten externen QTSP-Connector (siehe docs/services/
// signature-service.md "Offene Punkte").
export function SignaturesPanel({ document: activeDocument }: { document: DocumentSummary }) {
  const { accessToken, user } = useAuth();
  const { t } = useI18n();
  const [signatures, setSignatures] = useState<SignatureSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [level, setLevel] = useState<SignatureLevel>("ses");
  const [isSigning, setIsSigning] = useState(false);
  const [verifications, setVerifications] = useState<Record<number, SignatureVerification>>({});
  const [verifyingId, setVerifyingId] = useState<number | null>(null);

  useEffect(() => {
    setError(null);
    setVerifications({});
    if (!accessToken) return;
    listSignatures(accessToken, activeDocument.id)
      .then(setSignatures)
      .catch(() => setError(t("signatures.loadError")));
  }, [accessToken, activeDocument.id, t]);

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

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

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

// The signature format is currently exclusively PAdES (3.10) - the
// signature service rejects any other current version server-side with an
// error (`main.py`: `content_type != "application/pdf"`). Without this
// check, the UI offered the sign form for every document, even though a
// click was guaranteed to fail (user feedback).
const SIGNABLE_CONTENT_TYPE = "application/pdf";

// Attached using the same pattern as the OCR/rendition display in
// PreviewPane.tsx (its own list* call, its own load effect, non-blocking
// fallback UI) - placed below the metadata form, since this is
// document-bound but non-editable supplementary information (3.10, since
// P6-S7). QES is deliberately not selectable in the level selector - this
// baseline setup has no configured external QTSP connector (see
// docs/services/signature-service.md "Open Points").
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

  // Check the current version for signability (PDF only, see above) - its
  // own call instead of passing it down as a prop from `PreviewPane`,
  // since both components are mounted independently of each other.
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
      // The PAdES signature creates a new document version server-side
      // (ADR 0025) - PreviewPane doesn't know about this, since both
      // panels are mounted independently of each other (P23-S7).
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

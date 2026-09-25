"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  bindAdGroupToTeamspace,
  createTeamspace,
  createTeamspaceAppointment,
  createTeamspaceContact,
  deleteTeamspace,
  deleteTeamspaceAppointment,
  deleteTeamspaceContact,
  inviteTeamspaceMember,
  listAdGroupBindings,
  listTeamspaceAppointments,
  listTeamspaceContacts,
  listTeamspaceMembers,
  listTeamspaces,
  lookupUserByUsername,
  previewAdGroupMembers,
  removeTeamspaceMember,
  unbindAdGroupFromTeamspace,
  type AdGroupMemberPreview,
  type Teamspace,
  type TeamspaceAdGroupBinding,
  type TeamspaceAppointment,
  type TeamspaceContact,
  type TeamspaceMember,
} from "@/lib/api";
import { usePrincipalNames } from "@/lib/usePrincipalNames";

// Team workspace "Teamspace" (2.5, P14-S6) - self-managed, persistent
// group area. Master-detail view: list of teamspaces where
// `currentPrincipalId` is a member on the left (+ a create form), members/
// appointments/contacts of the selected teamspace on the right, including
// an "Ordner öffnen" jump into the regular document explorer (the root
// folder is an ordinary `folder-service` folder, see
// docs/services/teamspace-service.md).
export function TeamspacesPane({
  token,
  currentPrincipalId,
  onOpenFolder,
}: {
  token: string;
  currentPrincipalId: string;
  onOpenFolder: (folderId: string) => void;
}) {
  const { t } = useI18n();
  const [teamspaces, setTeamspaces] = useState<Teamspace[]>([]);
  const [selected, setSelected] = useState<Teamspace | null>(null);
  const [members, setMembers] = useState<TeamspaceMember[]>([]);
  const [appointments, setAppointments] = useState<TeamspaceAppointment[]>([]);
  const [contacts, setContacts] = useState<TeamspaceContact[]>([]);
  const [adGroupBindings, setAdGroupBindings] = useState<TeamspaceAdGroupBinding[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [inviteUsername, setInviteUsername] = useState("");
  const [appointmentTitle, setAppointmentTitle] = useState("");
  const [appointmentStart, setAppointmentStart] = useState("");
  const [appointmentEnd, setAppointmentEnd] = useState("");
  const [contactName, setContactName] = useState("");
  const [contactEmail, setContactEmail] = useState("");
  const [adGroupName, setAdGroupName] = useState("");
  const [adGroupPreview, setAdGroupPreview] = useState<AdGroupMemberPreview[] | null>(null);
  const [adGroupError, setAdGroupError] = useState<string | null>(null);

  const currentMember = members.find((m) => m.principal_id === currentPrincipalId) ?? null;
  const canManage = currentMember?.can_manage_members ?? false;
  // Reverse identity resolution (P19-S4, ADR 0069) - shows names instead
  // of raw principal_id UUIDs in the member list below.
  const principalNames = usePrincipalNames(
    token,
    members.map((m) => m.principal_id)
  );

  const reloadTeamspaces = useCallback(async () => {
    if (!token) return;
    setIsLoading(true);
    setError(null);
    try {
      setTeamspaces(await listTeamspaces(token));
    } catch {
      setError(t("teamspaces.loadError"));
    } finally {
      setIsLoading(false);
    }
  }, [token, t]);

  useEffect(() => {
    reloadTeamspaces();
  }, [reloadTeamspaces]);

  const reloadDetail = useCallback(
    async (teamspaceId: string) => {
      if (!token) return;
      try {
        const [memberList, appointmentList, contactList, bindingList] = await Promise.all([
          listTeamspaceMembers(token, teamspaceId),
          listTeamspaceAppointments(token, teamspaceId),
          listTeamspaceContacts(token, teamspaceId),
          listAdGroupBindings(token, teamspaceId),
        ]);
        setMembers(memberList);
        setAppointments(appointmentList);
        setContacts(contactList);
        setAdGroupBindings(bindingList);
      } catch {
        setError(t("teamspaces.loadError"));
      }
    },
    [token, t]
  );

  function selectTeamspace(teamspace: Teamspace) {
    setSelected(teamspace);
    reloadDetail(teamspace.id);
  }

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault();
    if (!newName.trim()) return;
    setError(null);
    try {
      const teamspace = await createTeamspace(token, {
        name: newName.trim(),
        description: newDescription,
      });
      setNewName("");
      setNewDescription("");
      await reloadTeamspaces();
      selectTeamspace(teamspace);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("teamspaces.createError"));
    }
  }

  async function handleDeleteTeamspace() {
    if (!selected) return;
    if (!window.confirm(t("teamspaces.deleteConfirm"))) return;
    setError(null);
    try {
      await deleteTeamspace(token, selected.id);
      setSelected(null);
      await reloadTeamspaces();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("teamspaces.deleteError"));
    }
  }

  async function handleLeave() {
    if (!selected) return;
    setError(null);
    try {
      await removeTeamspaceMember(token, selected.id, currentPrincipalId);
      setSelected(null);
      await reloadTeamspaces();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("teamspaces.leaveError"));
    }
  }

  async function handleInvite(event: React.FormEvent) {
    event.preventDefault();
    if (!selected || !inviteUsername.trim()) return;
    setError(null);
    try {
      const user = await lookupUserByUsername(token, inviteUsername.trim());
      await inviteTeamspaceMember(token, selected.id, { principalId: user.id });
      setInviteUsername("");
      await reloadDetail(selected.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("teamspaces.inviteError"));
    }
  }

  async function handleRemoveMember(principalId: string) {
    if (!selected) return;
    setError(null);
    try {
      await removeTeamspaceMember(token, selected.id, principalId);
      await reloadDetail(selected.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("teamspaces.removeMemberError"));
    }
  }

  async function handlePreviewAdGroup(event: React.FormEvent) {
    event.preventDefault();
    if (!selected || !adGroupName.trim()) return;
    setAdGroupError(null);
    setAdGroupPreview(null);
    try {
      setAdGroupPreview(await previewAdGroupMembers(token, selected.id, adGroupName.trim()));
    } catch (err) {
      setAdGroupError(err instanceof ApiError ? err.message : t("teamspaces.adGroupPreviewError"));
    }
  }

  async function handleBindAdGroup() {
    if (!selected || !adGroupName.trim()) return;
    setAdGroupError(null);
    try {
      await bindAdGroupToTeamspace(token, selected.id, adGroupName.trim());
      setAdGroupName("");
      setAdGroupPreview(null);
      await reloadDetail(selected.id);
    } catch (err) {
      setAdGroupError(err instanceof ApiError ? err.message : t("teamspaces.adGroupBindError"));
    }
  }

  async function handleUnbindAdGroup(name: string) {
    if (!selected) return;
    setError(null);
    try {
      await unbindAdGroupFromTeamspace(token, selected.id, name);
      await reloadDetail(selected.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("teamspaces.adGroupUnbindError"));
    }
  }

  async function handleCreateAppointment(event: React.FormEvent) {
    event.preventDefault();
    if (!selected || !appointmentTitle.trim() || !appointmentStart || !appointmentEnd) return;
    setError(null);
    try {
      await createTeamspaceAppointment(token, selected.id, {
        title: appointmentTitle.trim(),
        startAt: new Date(appointmentStart).toISOString(),
        endAt: new Date(appointmentEnd).toISOString(),
      });
      setAppointmentTitle("");
      setAppointmentStart("");
      setAppointmentEnd("");
      await reloadDetail(selected.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("teamspaces.appointmentError"));
    }
  }

  async function handleDeleteAppointment(appointmentId: number) {
    if (!selected) return;
    try {
      await deleteTeamspaceAppointment(token, selected.id, appointmentId);
      await reloadDetail(selected.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("teamspaces.appointmentError"));
    }
  }

  async function handleCreateContact(event: React.FormEvent) {
    event.preventDefault();
    if (!selected || !contactName.trim()) return;
    setError(null);
    try {
      await createTeamspaceContact(token, selected.id, {
        name: contactName.trim(),
        email: contactEmail.trim() || undefined,
      });
      setContactName("");
      setContactEmail("");
      await reloadDetail(selected.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("teamspaces.contactError"));
    }
  }

  async function handleDeleteContact(contactId: number) {
    if (!selected) return;
    try {
      await deleteTeamspaceContact(token, selected.id, contactId);
      await reloadDetail(selected.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("teamspaces.contactError"));
    }
  }

  const primaryBtn =
    "rounded-md border-0 bg-accent px-3 py-1.5 text-sm text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50";
  const secondaryBtn =
    "rounded-md border border-border bg-hover-bg px-3 py-1.5 text-sm text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50";
  const fieldInput =
    "box-border rounded-md border border-border bg-bg px-2 py-1.5 text-sm text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg";

  return (
    <section className="teamspaces-pane" aria-label={t("teamspaces.paneLabel")}>
      <h2 className="m-0 mb-3 text-base">{t("teamspaces.heading")}</h2>
      <p className="text-sm opacity-80">{t("teamspaces.hint")}</p>

      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}

      <form className="teamspaces-create-form" onSubmit={handleCreate}>
        <input
          type="text"
          className={fieldInput}
          placeholder={t("teamspaces.namePlaceholder")}
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
        />
        <input
          type="text"
          className={fieldInput}
          placeholder={t("teamspaces.descriptionPlaceholder")}
          value={newDescription}
          onChange={(e) => setNewDescription(e.target.value)}
        />
        <button type="submit" className={primaryBtn}>
          {t("teamspaces.createButton")}
        </button>
      </form>

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : teamspaces.length === 0 ? (
        <p className="italic opacity-70">{t("teamspaces.empty")}</p>
      ) : (
        <ul className="entry-list">
          {teamspaces.map((teamspace) => (
            <li className="entry-row" key={teamspace.id}>
              <span className="entry-name">{teamspace.name}</span>
              <span className="flex gap-2">
                <button type="button" className={secondaryBtn} onClick={() => selectTeamspace(teamspace)}>
                  {t("teamspaces.open")}
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}

      {selected && (
        <div className="teamspace-detail">
          <div className="flex items-center justify-between border-b border-border px-6 py-3">
            <h3>{selected.name}</h3>
            <span className="flex gap-2">
              <button type="button" className={secondaryBtn} onClick={() => onOpenFolder(selected.root_folder_id)}>
                {t("teamspaces.openFolder")}
              </button>
              <button type="button" className={secondaryBtn} onClick={handleLeave}>
                {t("teamspaces.leave")}
              </button>
              {canManage && (
                <button type="button" className={secondaryBtn} onClick={handleDeleteTeamspace}>
                  {t("teamspaces.deleteTeamspace")}
                </button>
              )}
            </span>
          </div>
          {selected.description && <p>{selected.description}</p>}

          <h4>{t("teamspaces.membersHeading")}</h4>
          <ul className="entry-list">
            {members.map((member) => (
              <li className="entry-row" key={member.id}>
                <span className="entry-name">
                  {principalNames[member.principal_id] ?? member.principal_id}
                  {member.can_manage_members ? ` (${t("teamspaces.manager")})` : ""}
                  {member.source_ad_group_name
                    ? ` (${t("teamspaces.viaAdGroup", { name: member.source_ad_group_name })})`
                    : ""}
                </span>
                {canManage && member.principal_id !== currentPrincipalId && (
                  <span className="flex gap-2">
                    <button type="button" className={secondaryBtn} onClick={() => handleRemoveMember(member.principal_id)}>
                      {t("teamspaces.removeMember")}
                    </button>
                  </span>
                )}
              </li>
            ))}
          </ul>
          {canManage && (
            <form className="teamspaces-invite-form" onSubmit={handleInvite}>
              <input
                type="text"
                className={fieldInput}
                placeholder={t("teamspaces.inviteUsernamePlaceholder")}
                value={inviteUsername}
                onChange={(e) => setInviteUsername(e.target.value)}
              />
              <button type="submit" className={primaryBtn}>
                {t("teamspaces.inviteButton")}
              </button>
            </form>
          )}

          <h4>{t("teamspaces.adGroupsHeading")}</h4>
          {adGroupBindings.length === 0 ? (
            <p className="italic opacity-70">{t("teamspaces.adGroupsEmpty")}</p>
          ) : (
            <ul className="entry-list">
              {adGroupBindings.map((binding) => (
                <li className="entry-row" key={binding.id}>
                  <span className="entry-name">{binding.ad_group_name}</span>
                  {canManage && (
                    <span className="flex gap-2">
                      <button
                        type="button"
                        className={secondaryBtn}
                        onClick={() => handleUnbindAdGroup(binding.ad_group_name)}
                      >
                        {t("teamspaces.adGroupUnbind")}
                      </button>
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
          {canManage && (
            <div className="teamspaces-ad-group-form">
              {adGroupError && (
                <p className="text-danger" role="alert">
                  {adGroupError}
                </p>
              )}
              <form onSubmit={handlePreviewAdGroup}>
                <input
                  type="text"
                  className={fieldInput}
                  placeholder={t("teamspaces.adGroupNamePlaceholder")}
                  value={adGroupName}
                  onChange={(e) => {
                    setAdGroupName(e.target.value);
                    setAdGroupPreview(null);
                  }}
                />
                <button type="submit" className={primaryBtn}>
                  {t("teamspaces.adGroupPreviewButton")}
                </button>
              </form>
              {adGroupPreview && (
                <div className="ad-group-preview">
                  {adGroupPreview.length === 0 ? (
                    <p className="italic opacity-70">{t("teamspaces.adGroupPreviewEmpty")}</p>
                  ) : (
                    <ul className="entry-list">
                      {adGroupPreview.map((member) => (
                        <li className="entry-row" key={member.id}>
                          <span className="entry-name">{member.username}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                  <button type="button" className={primaryBtn} onClick={handleBindAdGroup}>
                    {t("teamspaces.adGroupBindButton")}
                  </button>
                </div>
              )}
            </div>
          )}

          <h4>{t("teamspaces.appointmentsHeading")}</h4>
          <ul className="entry-list">
            {appointments.map((appointment) => (
              <li className="entry-row" key={appointment.id}>
                <span className="entry-name">
                  {appointment.title} —{" "}
                  {new Date(appointment.start_at).toLocaleString()} –{" "}
                  {new Date(appointment.end_at).toLocaleString()}
                </span>
                <span className="flex gap-2">
                  <button type="button" className={secondaryBtn} onClick={() => handleDeleteAppointment(appointment.id)}>
                    {t("common.delete")}
                  </button>
                </span>
              </li>
            ))}
          </ul>
          <form className="teamspaces-appointment-form" onSubmit={handleCreateAppointment}>
            <input
              type="text"
              className={fieldInput}
              placeholder={t("teamspaces.appointmentTitlePlaceholder")}
              value={appointmentTitle}
              onChange={(e) => setAppointmentTitle(e.target.value)}
            />
            <input
              type="datetime-local"
              className={fieldInput}
              value={appointmentStart}
              onChange={(e) => setAppointmentStart(e.target.value)}
            />
            <input
              type="datetime-local"
              className={fieldInput}
              value={appointmentEnd}
              onChange={(e) => setAppointmentEnd(e.target.value)}
            />
            <button type="submit" className={primaryBtn}>
              {t("teamspaces.addAppointmentButton")}
            </button>
          </form>

          <h4>{t("teamspaces.contactsHeading")}</h4>
          <ul className="entry-list">
            {contacts.map((contact) => (
              <li className="entry-row" key={contact.id}>
                <span className="entry-name">
                  {contact.name}
                  {contact.email ? ` (${contact.email})` : ""}
                </span>
                <span className="flex gap-2">
                  <button type="button" className={secondaryBtn} onClick={() => handleDeleteContact(contact.id)}>
                    {t("common.delete")}
                  </button>
                </span>
              </li>
            ))}
          </ul>
          <form className="teamspaces-contact-form" onSubmit={handleCreateContact}>
            <input
              type="text"
              className={fieldInput}
              placeholder={t("teamspaces.contactNamePlaceholder")}
              value={contactName}
              onChange={(e) => setContactName(e.target.value)}
            />
            <input
              type="email"
              className={fieldInput}
              placeholder={t("teamspaces.contactEmailPlaceholder")}
              value={contactEmail}
              onChange={(e) => setContactEmail(e.target.value)}
            />
            <button type="submit" className={primaryBtn}>
              {t("teamspaces.addContactButton")}
            </button>
          </form>
        </div>
      )}
    </section>
  );
}

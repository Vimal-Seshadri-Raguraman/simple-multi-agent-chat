/**
 * Wire types for the server's JSON responses (`app/schemas.py`). Kept as
 * plain TS interfaces with the SAME field names/casing the server sends
 * (including the message payload's odd PascalCase `Sender`/`Message`/
 * `Channel` keys, `app.schemas.build_message_payload`'s module
 * docstring) -- no client-side renaming, so a field-name mismatch shows
 * up immediately as a `TS2339` rather than a silent `undefined` at
 * runtime.
 */

export type MetaOut = {
  server_version: string;
  api_version: number;
};

export type TokenPairOut = {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
};

export type AccountOut = {
  account_id: string;
  email: string | null;
  created_at: string;
};

/** One of the caller's workspace profiles (`POST /accounts/login`, `GET /accounts/me`). */
export type Membership = {
  workspace_id: string;
  workspace_name: string;
  member_id: string;
  handle: string;
};

export type WorkspaceOut = {
  workspace_id: string;
  workspace_name: string;
  visibility: string;
};

/** `GET /accounts/me`: the caller's own account plus every workspace
 * membership it holds -- the Rail workspace switcher's source (task-3
 * brief, web spec §2's "switcher menu: your memberships + create/join";
 * `state/auth.tsx`'s `memberships` only survives until a workspace is
 * entered, so the authed shell re-fetches this itself rather than
 * threading the login-time list through). */
export type AccountMeOut = {
  account_id: string;
  email: string | null;
  created_at: string;
  memberships: Membership[];
};

export type WorkspaceSearchOut = WorkspaceOut;

export type ChannelOut = {
  channel_id: string;
  channel_name: string;
};

export type MemberOut = {
  member_id: string;
  member_name: string;
  member_type: string;
  handle: string;
  created_at: string;
  account_id: string;
  first_name?: string | null;
  last_name?: string | null;
  company?: string | null;
  occupation?: string | null;
  job_role?: string | null;
};

export type MemberSelfOut = {
  member_id: string;
  member_name: string;
  member_type: string;
  handle: string;
  workspace_id: string;
  account_id: string;
  created_at: string;
  first_name: string | null;
  last_name: string | null;
  company: string | null;
  occupation: string | null;
  job_role: string | null;
  is_admin: boolean | null;
  workspace_visibility: string | null;
};

export type MemberRegisterOut = {
  member_id: string;
  member_name: string;
  member_type: string;
  handle: string;
  api_key: string;
};

export type InviteOut = {
  invite_id: string;
  workspace_id: string;
  invite_type: string;
  email: string | null;
  code: string | null;
  created_by: string;
  created_at: string;
  expires_at: string | null;
};

export type UnreadsRowOut = {
  channel_id: string;
  channel_name: string;
  unread_count: number;
  first_unread_message_id: string | null;
  mention_count: number;
};

export type UnreadsOut = {
  unreads: UnreadsRowOut[];
};

/** `app.schemas.build_message_payload`'s wire shape -- shared by REST and WebSocket. */
export type MessagePayload = {
  timestamp: string;
  workspace: { workspace_id: string; workspace_name: string };
  Channel: { channel_id: string; channel_name: string };
  Sender: { member_id: string; member_name: string };
  Message: { message_id: string; message_text: string };
  mentions: Array<{ member_id: string; handle: string; member_name: string }>;
  channel_refs: Array<{ channel_id: string; channel_name: string }>;
};

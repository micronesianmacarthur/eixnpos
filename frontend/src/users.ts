/**
 * Shared user-management types and helpers used by both the Settings page and
 * the dedicated Users page. Centralising the identity prefill and the
 * save-payload builder keeps the two surfaces from re-implementing (and
 * diverging on) how a user edit is constructed.
 */

export interface User {
  id: number;
  email: string;
  name: string;
  /** Raw identity fields exposed by UserSerializer. */
  username?: string;
  first_name?: string;
  last_name?: string;
  role: string;
  active?: boolean;
  createdAt?: string;
  updatedAt?: string;
}

export interface UserForm {
  username: string;
  email: string;
  first_name: string;
  last_name: string;
  role: string;
  password: string;
}

export const USER_ROLES = ['cashier', 'manager', 'admin'] as const;

export const EMPTY_USER_FORM: UserForm = {
  username: '',
  email: '',
  first_name: '',
  last_name: '',
  role: 'cashier',
  password: '',
};

/**
 * Prefill the edit form from a user's real fields. We fall back to the combined
 * name only if the backend did not send raw identity fields (defensive; the
 * fixed UserSerializer always sends them).
 */
export function userFormFromUser(user: User): UserForm {
  return {
    username: user.username ?? user.name ?? '',
    email: user.email,
    first_name: user.first_name ?? '',
    last_name: user.last_name ?? '',
    role: user.role,
    password: '',
  };
}

export interface UserSaveResult {
  payload: Record<string, string>;
  error?: string;
}

/**
 * Build the POST (create) or PATCH (edit) payload for the user form.
 *
 * For edits only fields that actually differ from the saved user are included,
 * so editing e.g. a role never rewrites names that the user did not touch
 * (username itself is immutable server-side and never sent on edit).
 */
export function buildUserSaveRequest(
  mode: 'create' | 'edit',
  form: UserForm,
  original: User | null,
): UserSaveResult {
  const username = form.username.trim();
  const email = form.email.trim();
  const firstName = form.first_name.trim();
  const lastName = form.last_name.trim();
  const role = form.role;
  const password = form.password;

  if (mode === 'create') {
    if (!username) return { payload: {}, error: 'Username is required.' };
    if (!firstName && !lastName) {
      return { payload: {}, error: 'At least first name or last name is required.' };
    }
    if (!password) return { payload: {}, error: 'Password is required for new users.' };
  }
  if (!USER_ROLES.includes(role as (typeof USER_ROLES)[number])) {
    return { payload: {}, error: 'Invalid role selected.' };
  }
  if (password && password.length < 8) {
    return { payload: {}, error: 'Password must be at least 8 characters.' };
  }

  const payload: Record<string, string> = {};
  if (mode === 'create') {
    payload.username = username;
    payload.email = email;
    payload.first_name = firstName;
    payload.last_name = lastName;
    payload.role = role;
  } else {
    if (email !== (original?.email ?? '')) payload.email = email;
    if (role !== (original?.role ?? '')) payload.role = role;
    if (firstName !== (original?.first_name ?? '')) payload.first_name = firstName;
    if (lastName !== (original?.last_name ?? '')) payload.last_name = lastName;
  }
  if (password) payload.password = password;

  return { payload };
}

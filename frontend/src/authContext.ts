import { createContext, useContext } from 'react';

export type Role = 'cashier' | 'manager' | 'admin';

export interface CurrentUser {
  id: number;
  username: string;
  role: Role;
}

export interface LoginResponse {
  user: CurrentUser;
  password_change_required: boolean;
  /**
   * Transitional fallback for servers that still echo the bare
   * `must_change_password` field in the login payload instead of the
   * contracted `password_change_required`. Remove once the rename lands.
   */
  must_change_password?: boolean;
  business_info_complete: boolean;
}

export interface MeResponse {
  user: CurrentUser;
  must_change_password: boolean;
  business_info_complete: boolean;
}

export interface AuthContextValue {
  me: MeResponse | null;
  loading: boolean;
  error: string;
  refresh: () => void;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}

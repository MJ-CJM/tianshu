import { createContext, useContext } from "react";
import type { Principal, RuntimeMode } from "../api/auth";

export type AuthStatus = "checking" | "anonymous" | "authenticated" | "error";

export interface AuthContextValue {
  status: AuthStatus;
  mode: RuntimeMode | null;
  principal: Principal | null;
  login: (token: string) => Promise<void>;
  logout: () => Promise<void>;
  retry: () => void;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within AuthProvider");
  return context;
}

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  AuthApiError,
  createAuthSession,
  deleteAuthSession,
  getAuthMe,
  getAuthMode,
  type Principal,
  type RuntimeMode,
} from "../api/auth";
import { subscribeAuthExpired } from "../api/authFetch";
import {
  AuthContext,
  type AuthContextValue,
  type AuthStatus,
} from "./AuthContext";

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<AuthStatus>("checking");
  const [mode, setMode] = useState<RuntimeMode | null>(null);
  const [principal, setPrincipal] = useState<Principal | null>(null);
  const [discoveryVersion, setDiscoveryVersion] = useState(0);

  useEffect(() => {
    let active = true;

    void (async () => {
      let discoveredMode: RuntimeMode | null = null;
      try {
        const modeResponse = await getAuthMode();
        discoveredMode = modeResponse.mode;
        if (!active) return;
        setMode(discoveredMode);

        const me = await getAuthMe();
        if (!active) return;
        setPrincipal(me.principal);
        setStatus("authenticated");
      } catch (error) {
        if (!active) return;
        if (
          discoveredMode === "secure-remote" &&
          error instanceof AuthApiError &&
          error.status === 401
        ) {
          setPrincipal(null);
          setStatus("anonymous");
          return;
        }
        setStatus("error");
      }
    })();

    return () => {
      active = false;
    };
  }, [discoveryVersion]);

  useEffect(
    () =>
      subscribeAuthExpired(() => {
        if (mode !== "secure-remote") return;
        queryClient.clear();
        setPrincipal(null);
        setStatus("anonymous");
      }),
    [mode, queryClient],
  );

  const login = useCallback(
    async (token: string) => {
      const session = await createAuthSession(token.trim());
      queryClient.clear();
      setPrincipal(session.principal);
      setStatus("authenticated");
    },
    [queryClient],
  );

  const logout = useCallback(async () => {
    await deleteAuthSession();
    queryClient.clear();
    setPrincipal(null);
    if (mode === "secure-remote") setStatus("anonymous");
    else {
      setStatus("checking");
      setDiscoveryVersion((version) => version + 1);
    }
  }, [mode, queryClient]);

  const retry = useCallback(() => {
    setStatus("checking");
    setPrincipal(null);
    setDiscoveryVersion((version) => version + 1);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ status, mode, principal, login, logout, retry }),
    [status, mode, principal, login, logout, retry],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

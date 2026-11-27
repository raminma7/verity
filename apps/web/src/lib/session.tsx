"use client";

import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import { setLocale } from "@/i18n";

import { api, ApiError, type User, type WorkspaceDetail, type WorkspaceSummary } from "./api";

type SessionState = {
  user: User;
  workspaces: WorkspaceSummary[];
  current: WorkspaceDetail | null;
  can: (perm: string) => boolean;
  refresh: () => Promise<void>;
  switchWorkspace: (id: string) => Promise<void>;
};

const Ctx = createContext<SessionState | null>(null);

export function useSession(): SessionState {
  const s = useContext(Ctx);
  if (!s) throw new Error("useSession outside SessionProvider");
  return s;
}

/** Loads the signed-in user and current workspace; sends people who can't be here to the right auth step. */
export function SessionProvider({ children, fallback }: { children: ReactNode; fallback: ReactNode }) {
  const router = useRouter();
  const [state, setState] = useState<{ user: User; workspaces: WorkspaceSummary[]; current: WorkspaceDetail | null } | null>(null);

  const load = useCallback(async () => {
    try {
      const me = await api.get<{ user: User; workspaces: WorkspaceSummary[] }>("/me");
      if (!me.user.email_verified) {
        router.replace(`/verify-email?email=${encodeURIComponent(me.user.email)}`);
        return;
      }
      let current: WorkspaceDetail | null = null;
      if (me.workspaces.length) {
        try {
          current = await api.get<WorkspaceDetail>("/workspace");
        } catch (e) {
          // Last workspace gone (removed or left): fall back to the first one.
          if (e instanceof ApiError && (e.code === "workspace_not_found" || e.code === "workspace_required")) {
            await api.post(`/me/switch-workspace/${me.workspaces[0].id}`);
            current = await api.get<WorkspaceDetail>("/workspace");
          } else if (!(e instanceof ApiError && e.code === "workspace_suspended")) {
            throw e;
          }
        }
      }
      await setLocale(me.user.locale); // before anything renders, so every screen is in the user's language
      setState({ user: me.user, workspaces: me.workspaces, current });
    } catch (e) {
      if (e instanceof ApiError && e.code === "mfa_required") router.replace("/sign-in/2fa");
      else if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
        const next = typeof window !== "undefined" ? window.location.pathname + window.location.search : "/dashboard";
        router.replace(`/sign-in?next=${encodeURIComponent(next)}${e.code === "account_suspended" ? "&suspended=1" : ""}`);
      } else throw e;
    }
  }, [router]);

  useEffect(() => {
    load();
  }, [load]);

  const switchWorkspace = useCallback(
    async (id: string) => {
      await api.post(`/me/switch-workspace/${id}`);
      await load();
      router.push("/dashboard");
    },
    [load, router],
  );

  if (!state) return <>{fallback}</>;
  const perms = new Set(state.current?.permissions ?? []);
  return (
    <Ctx.Provider value={{ ...state, can: (p) => perms.has(p), refresh: load, switchWorkspace }}>{children}</Ctx.Provider>
  );
}

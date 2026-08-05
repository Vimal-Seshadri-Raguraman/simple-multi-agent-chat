/**
 * Hand-rolled auth/screen-state machine: React context + reducer, no
 * `react-router` (YAGNI, task-2 brief -- the app only ever has two top-
 * level shapes: the unauthenticated auth screens below, or the authed
 * shell Task 3 owns). Every auth screen (`screens/*.tsx`) reads/dispatches
 * through `useAuth()` rather than holding its own copy of session state.
 */

import {
  type ReactNode,
  createContext,
  useCallback,
  useContext,
  useMemo,
  useReducer,
} from "react";
import * as api from "../lib/api";
import type { Membership, Session } from "../lib/api";

/**
 * Every screen the unauthenticated (or not-yet-in-a-workspace) app can
 * be in, plus the terminal `"authed"` state Task 3's shell renders.
 */
export type Screen =
  | "welcome"
  | "login"
  | "register"
  | "workspace-picker"
  | "create-or-join"
  | "join"
  | "authed";

export type AuthState = {
  screen: Screen;
  session: Session | null;
  /** Populated after a `login()` whose account has >1 workspace membership. */
  memberships: Membership[];
  /** Set while an async auth action (login/signup/join/...) is in flight. */
  pending: boolean;
  /** The last auth action's error message, if any -- cleared on the next attempt. */
  error: string | null;
};

type Action =
  | { type: "NAVIGATE"; screen: Screen }
  | { type: "PENDING" }
  | { type: "ERROR"; message: string }
  | { type: "ACCOUNT_READY"; session: Session }
  | { type: "LOGIN_SUCCESS"; session: Session; workspaces: Membership[] }
  | { type: "WORKSPACE_ENTERED"; session: Session }
  | { type: "LOGGED_OUT" };

function initialState(): AuthState {
  const session = api.getSession();
  return {
    screen: session && session.workspaceId ? "authed" : "welcome",
    session,
    memberships: [],
    pending: false,
    error: null,
  };
}

function reducer(state: AuthState, action: Action): AuthState {
  switch (action.type) {
    case "NAVIGATE":
      return { ...state, screen: action.screen, error: null };
    case "PENDING":
      return { ...state, pending: true, error: null };
    case "ERROR":
      return { ...state, pending: false, error: action.message };
    case "ACCOUNT_READY":
      // signup() success: an account with no workspace yet -> the
      // create-or-join step (register's "two-step", brief-pinned order:
      // account fields land BEFORE the workspace step is ever shown).
      return {
        ...state,
        pending: false,
        error: null,
        session: action.session,
        screen: "create-or-join",
      };
    case "LOGIN_SUCCESS": {
      if (action.workspaces.length === 0) {
        return {
          ...state,
          pending: false,
          error: null,
          session: action.session,
          memberships: [],
          screen: "create-or-join",
        };
      }
      if (action.workspaces.length === 1) {
        // Single-membership auto-enter is driven by the Login screen
        // (it needs to await enterWorkspace() and dispatch
        // WORKSPACE_ENTERED itself) -- this branch just records the
        // pending state; Login.tsx handles the follow-up call.
        return {
          ...state,
          pending: true,
          error: null,
          session: action.session,
          memberships: action.workspaces,
        };
      }
      return {
        ...state,
        pending: false,
        error: null,
        session: action.session,
        memberships: action.workspaces,
        screen: "workspace-picker",
      };
    }
    case "WORKSPACE_ENTERED":
      return {
        ...state,
        pending: false,
        error: null,
        session: action.session,
        memberships: [],
        screen: "authed",
      };
    case "LOGGED_OUT":
      return {
        screen: "welcome",
        session: null,
        memberships: [],
        pending: false,
        error: null,
      };
    default:
      return state;
  }
}

export type AuthContextValue = AuthState & {
  navigate: (screen: Screen) => void;
  setPending: () => void;
  setError: (message: string) => void;
  /** Record a freshly-created account-only session (register step 1). */
  accountReady: (session: Session) => void;
  /** Record a successful login's session + memberships. */
  loginSuccess: (session: Session, workspaces: Membership[]) => void;
  /** Record a session that just gained (or switched) a workspace. */
  workspaceEntered: (session: Session) => void;
  /** Log out locally (and best-effort server-side via `api.logout()`). */
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, undefined, initialState);

  const navigate = useCallback((screen: Screen) => dispatch({ type: "NAVIGATE", screen }), []);
  const setPending = useCallback(() => dispatch({ type: "PENDING" }), []);
  const setError = useCallback((message: string) => dispatch({ type: "ERROR", message }), []);
  const accountReady = useCallback(
    (session: Session) => dispatch({ type: "ACCOUNT_READY", session }),
    []
  );
  const loginSuccess = useCallback(
    (session: Session, workspaces: Membership[]) =>
      dispatch({ type: "LOGIN_SUCCESS", session, workspaces }),
    []
  );
  const workspaceEntered = useCallback(
    (session: Session) => dispatch({ type: "WORKSPACE_ENTERED", session }),
    []
  );
  const logout = useCallback(async () => {
    await api.logout();
    dispatch({ type: "LOGGED_OUT" });
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      ...state,
      navigate,
      setPending,
      setError,
      accountReady,
      loginSuccess,
      workspaceEntered,
      logout,
    }),
    [state, navigate, setPending, setError, accountReady, loginSuccess, workspaceEntered, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (ctx === null) {
    throw new Error("useAuth() must be called within an <AuthProvider>");
  }
  return ctx;
}

/**
 * Convenience hook for the app root (Task 3's shell composes this):
 * `true` once the auth store has landed on `"authed"`.
 */
export function useIsAuthed(): boolean {
  const { screen } = useAuth();
  return screen === "authed";
}

// Re-exported so screens/tests importing from "../state/auth" don't also
// need a separate import from "../lib/api" just for the type.
export type { Membership, Session };

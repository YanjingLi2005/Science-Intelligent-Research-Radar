import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react';
import {
  clearAuthToken,
  getAuthToken,
  getMe,
  loginAccount,
  logoutAccount,
  registerAccount,
} from '../api';

type AuthState = 'loading' | 'authed' | 'anon';

interface AuthContextValue {
  state: AuthState;
  username: string | null;
  role: 'admin' | 'user' | null;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>(() => (
    getAuthToken() ? 'loading' : 'anon'
  ));
  const [username, setUsername] = useState<string | null>(null);
  const [role, setRole] = useState<'admin' | 'user' | null>(null);

  useEffect(() => {
    let cancelled = false;
    const token = getAuthToken();
    if (!token) {
      return;
    }
    getMe()
      .then((user) => {
        if (!cancelled) {
          setUsername(user.username);
          setRole(user.role);
          setState('authed');
        }
      })
      .catch(() => {
        if (!cancelled) {
          clearAuthToken();
          setUsername(null);
          setRole(null);
          setState('anon');
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Any request that returns 401 clears the token and flips back to the login
  // screen (e.g. the session was revoked server-side).
  useEffect(() => {
    const onUnauthorized = () => {
      clearAuthToken();
      setUsername(null);
      setRole(null);
      setState('anon');
    };
    window.addEventListener('radar:unauthorized', onUnauthorized);
    return () => window.removeEventListener('radar:unauthorized', onUnauthorized);
  }, []);

  const login = useCallback(async (name: string, password: string) => {
    const result = await loginAccount(name, password);
    setUsername(result.username);
    setRole(result.role);
    setState('authed');
  }, []);

  const register = useCallback(async (name: string, password: string) => {
    await registerAccount(name, password);
    // Registration creates the account; log the user straight in.
    const result = await loginAccount(name, password);
    setUsername(result.username);
    setRole(result.role);
    setState('authed');
  }, []);

  const logout = useCallback(async () => {
    await logoutAccount();
    setUsername(null);
    setRole(null);
    setState('anon');
  }, []);

  return (
    <AuthContext.Provider value={{ state, username, role, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}

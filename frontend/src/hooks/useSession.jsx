/* Session analyste : sonde au démarrage, connexion, déconnexion, expiration. */

import {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
} from "react";
import * as api from "../lib/api.js";

const SessionContext = createContext(null);

export function SessionProvider({ children }) {
  const [user, setUser] = useState(null);
  const [booting, setBooting] = useState(true);
  const [expiredMessage, setExpiredMessage] = useState("");

  // Un 401 sur n'importe quelle route ramène à l'écran de connexion.
  useEffect(() => {
    api.setUnauthorizedHandler(() => {
      setUser((current) => {
        if (current) {
          setExpiredMessage("Votre session a expiré. Reconnectez-vous pour continuer.");
        }
        return null;
      });
    });
    return () => api.setUnauthorizedHandler(null);
  }, []);

  useEffect(() => {
    let cancelled = false;
    api.auth
      .me()
      .then((me) => {
        if (!cancelled) setUser(me);
      })
      .catch(() => {
        /* pas de session : écran de connexion */
      })
      .finally(() => {
        if (!cancelled) setBooting(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (username, password) => {
    const me = await api.auth.login(username, password);
    setExpiredMessage("");
    setUser(me);
    return me;
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.auth.logout();
    } catch {
      /* la session est de toute façon abandonnée côté client */
    }
    setExpiredMessage("");
    setUser(null);
  }, []);

  const value = useMemo(
    () => ({ user, booting, login, logout, expiredMessage }),
    [user, booting, login, logout, expiredMessage]
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession() {
  const context = useContext(SessionContext);
  if (!context) throw new Error("useSession doit être utilisé dans un SessionProvider.");
  return context;
}

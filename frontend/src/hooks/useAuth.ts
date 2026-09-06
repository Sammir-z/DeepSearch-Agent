import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  getCurrentUser,
  loginUser,
  logoutUser,
  registerUser
} from "../lib/api";
import type { AuthPayload, AuthUser } from "../types";

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

export function useAuth() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setIsLoading(true);
    try {
      const currentUser = await getCurrentUser();
      setUser(currentUser);
      setError("");
      return currentUser;
    } catch (requestError) {
      if (!(requestError instanceof ApiError && requestError.status === 401)) {
        setError(errorMessage(requestError, "认证服务暂时不可用"));
      }
      setUser(null);
      return null;
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const login = useCallback(async (payload: AuthPayload) => {
    setIsSubmitting(true);
    setError("");
    try {
      const authenticatedUser = await loginUser(payload);
      setUser(authenticatedUser);
      return authenticatedUser;
    } catch (requestError) {
      const message = errorMessage(requestError, "登录失败");
      setError(message);
      throw requestError;
    } finally {
      setIsSubmitting(false);
    }
  }, []);

  const register = useCallback(async (payload: AuthPayload) => {
    setIsSubmitting(true);
    setError("");
    try {
      await registerUser(payload);
      // Registration intentionally does not expose a token.  Log in through
      // the same cookie flow immediately after the account is created.
      const authenticatedUser = await loginUser(payload);
      setUser(authenticatedUser);
      return authenticatedUser;
    } catch (requestError) {
      const message = errorMessage(requestError, "注册失败");
      setError(message);
      throw requestError;
    } finally {
      setIsSubmitting(false);
    }
  }, []);

  const logout = useCallback(async () => {
    setIsSubmitting(true);
    setError("");
    try {
      await logoutUser();
    } catch (requestError) {
      setError(errorMessage(requestError, "退出登录失败"));
      throw requestError;
    } finally {
      // Even if the server is temporarily unavailable, never leave the local
      // workspace attached to the previous user's identity.
      setUser(null);
      setIsSubmitting(false);
    }
  }, []);

  const expireSession = useCallback(() => {
    setUser(null);
    setError("登录状态已过期，请重新登录");
  }, []);

  return {
    error,
    expireSession,
    isLoading,
    isSubmitting,
    login,
    logout,
    refresh,
    register,
    user
  };
}

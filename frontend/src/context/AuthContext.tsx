/**
 * Authentication and User Role Context
 * 
 * Authority:
 *   - PRODUCTIZATION_SPEC.md §1.2 (Role separation)
 *   - PRODUCTIZATION_DECISIONS.md PD-009, PD-013
 */

import React, { createContext, useContext, useState, useEffect } from 'react';
import type { AuthenticatedUser, CanonicalRole } from '../types';
import { api } from '../api/client';

interface AuthContextType {
  token: string | null;
  user: AuthenticatedUser | null;
  activeRole: CanonicalRole;
  isAuthenticated: boolean;
  setAuth: (token: string | null, user: AuthenticatedUser | null) => void;
  setActiveRole: (role: CanonicalRole) => void;
  logout: () => void;
}

const DEFAULT_DEV_USER: AuthenticatedUser = {
  userId: '00000000-0000-0000-0000-000000000001',
  username: 'reviewer.clindoc',
  fullName: 'Dr. Jane Smith (Reviewer)',
  email: 'reviewer@clindoc.hospital.local',
  roles: ['clinical_reviewer'],
};

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [token, setTokenState] = useState<string | null>(() => {
    return localStorage.getItem('clindoc_auth_token') || 'dev-mock-bearer-token';
  });

  const [user, setUser] = useState<AuthenticatedUser | null>(() => {
    const saved = localStorage.getItem('clindoc_auth_user');
    if (saved) {
      try {
        return JSON.parse(saved);
      } catch {
        return DEFAULT_DEV_USER;
      }
    }
    return DEFAULT_DEV_USER;
  });

  const [activeRole, setActiveRole] = useState<CanonicalRole>(() => {
    return (user?.roles[0] as CanonicalRole) || 'clinical_reviewer';
  });

  useEffect(() => {
    api.setToken(token);
    if (token) {
      localStorage.setItem('clindoc_auth_token', token);
    } else {
      localStorage.removeItem('clindoc_auth_token');
    }
  }, [token]);

  useEffect(() => {
    if (user) {
      localStorage.setItem('clindoc_auth_user', JSON.stringify(user));
      if (!user.roles.includes(activeRole)) {
        setActiveRole(user.roles[0] || 'clinical_reviewer');
      }
    } else {
      localStorage.removeItem('clindoc_auth_user');
    }
  }, [user]);

  const setAuth = (newToken: string | null, newUser: AuthenticatedUser | null) => {
    setTokenState(newToken);
    setUser(newUser);
  };

  const logout = () => {
    setTokenState(null);
    setUser(null);
  };

  return (
    <AuthContext.Provider
      value={{
        token,
        user,
        activeRole,
        isAuthenticated: !!token && !!user,
        setAuth,
        setActiveRole,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = (): AuthContextType => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};

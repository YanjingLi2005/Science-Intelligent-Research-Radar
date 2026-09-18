import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { type Project, type CaseSummary, getCases, getCaseDetail } from '../api';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface AgentEntry {
  id: string;
  name: string;
  type: string;
  enabled: boolean;
}

interface ProjectContextValue {
  /** Current active project (full detail).  null while loading / unselected. */
  project: Project | null;
  /** Current project id.  Empty string when no project is selected. */
  projectId: string;
  /** True while a project switch is loading (pages gate on this). */
  loading: boolean;
  /** True during background refreshes — pages must NOT gate on this. */
  refreshing: boolean;
  /** Error from the last initial load, if any. */
  error: Error | null;
  /** Error from the last background refresh (banner-level, not page-level). */
  refreshError: Error | null;
  /** All available projects (lightweight list). */
  caseList: CaseSummary[];
  /** Switch to a different project. */
  switchProject: (id: string) => Promise<void>;
  /** Leave the current project and return to the project list. */
  clearProject: () => void;
  /** Refresh the current project from the API. */
  refreshProject: () => Promise<void>;
  /** Refresh the case list. */
  refreshCaseList: () => Promise<void>;
  /** Currently enabled agents for this project (reserved for future use). */
  agents: AgentEntry[];
}

const ProjectContext = createContext<ProjectContextValue | null>(null);

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export function ProjectProvider({ children }: { children: React.ReactNode }) {
  const [projectId, setProjectId] = useState('');
  const [project, setProject] = useState<Project | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [refreshError, setRefreshError] = useState<Error | null>(null);
  const [caseList, setCaseList] = useState<CaseSummary[]>([]);
  const [agents] = useState<AgentEntry[]>([]);  // reserved for future Agent hub
  // Monotonic request token: the LAST issued request wins. Without it, two
  // rapid switches can leave project and projectId from different cases.
  const requestTokenRef = useRef(0);

  // ---- internal helpers ----

  // ---- public actions ----

  const switchProject = useCallback(async (id: string) => {
    const requestId = ++requestTokenRef.current;
    setLoading(true);
    setError(null);
    try {
      const p = await getCaseDetail(id);
      if (requestId !== requestTokenRef.current) return; // stale response
      setProject(p);
      setProjectId(id);
    } catch (e) {
      if (requestId !== requestTokenRef.current) return;
      const nextError = e instanceof Error ? e : new Error(String(e));
      setProject(null);
      setProjectId('');
      setError(nextError);
      throw nextError;
    } finally {
      if (requestId === requestTokenRef.current) setLoading(false);
    }
  }, []);

  const clearProject = useCallback(() => {
    setProjectId('');
    setProject(null);
    setError(null);
    setRefreshError(null);
  }, []);

  const refreshProject = useCallback(async () => {
    if (!projectId) return;
    const requestId = ++requestTokenRef.current;
    setRefreshing(true);
    try {
      const p = await getCaseDetail(projectId);
      if (requestId !== requestTokenRef.current) return;
      setProject(p);
      setError(null);
      setRefreshError(null);
    } catch (e) {
      if (requestId !== requestTokenRef.current) return;
      // A background refresh failure must not be misattributed to the action
      // that triggered it, and must not flip pages to the full error view.
      setRefreshError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      if (requestId === requestTokenRef.current) setRefreshing(false);
    }
  }, [projectId]);

  const refreshCaseList = useCallback(async () => {
    try {
      const cases = await getCases();
      setCaseList(cases);
      setError(null);
    } catch (e) {
      // Keep the previously loaded list on transient errors — wiping it
      // would make the app claim there are no projects.
      const nextError = e instanceof Error ? e : new Error(String(e));
      setError(nextError);
      throw nextError;
    }
  }, []);

  // ---- initial load ----

  useEffect(() => {
    void refreshCaseList().catch(() => undefined);
  }, [refreshCaseList]);

  // ---- context value ----

  const value = useMemo<ProjectContextValue>(() => ({
    project, projectId, loading, refreshing, error, refreshError, caseList,
    switchProject, clearProject, refreshProject, refreshCaseList, agents,
  }), [
    agents, caseList, clearProject, error, loading, project, projectId,
    refreshCaseList, refreshError, refreshing, refreshProject, switchProject,
  ]);

  return <ProjectContext.Provider value={value}>{children}</ProjectContext.Provider>;
}

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

export function useProject(): ProjectContextValue {
  const ctx = useContext(ProjectContext);
  if (!ctx) throw new Error('useProject must be used within ProjectProvider');
  return ctx;
}

/** Convenience hook: returns projectId (for pages that only need the id). */
export function useProjectId(): string {
  return useProject().projectId;
}

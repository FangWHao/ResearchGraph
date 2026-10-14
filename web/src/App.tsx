import { useCallback, useEffect, useState } from 'react';
import { api } from './api';
import { NewProject } from './NewProject';
import { ProjectSettings } from './ProjectSettings';
import { ProjectView } from './ProjectView';
import { Setup } from './Setup';
import type { AppState } from './types';

function hashProject(): number | null {
  const match = /p=(\d+)/.exec(window.location.hash);
  return match ? Number(match[1]) : null;
}

export function App() {
  const [state, setState] = useState<AppState | null>(null);
  const [view, setView] = useState<'main' | 'setup' | 'new' | 'project'>('main');
  const [projectId, setProjectId] = useState<number | null>(hashProject);
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    try {
      setState(await api.state());
      setError('');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (projectId !== null) window.history.replaceState(null, '', `#p=${projectId}`);
  }, [projectId]);

  if (!state) {
    return <div className="page">{error ? <div className="error">无法连接本机服务：{error}</div> : <div className="muted">加载中…</div>}</div>;
  }
  const noSources = state.sources.length === 0;
  if (view === 'setup' || noSources) {
    return (
      <Setup
        state={state}
        first={noSources}
        onDone={(next) => {
          setState(next);
          setView('main');
        }}
        onCancel={noSources ? undefined : () => setView('main')}
      />
    );
  }
  if (view === 'new' || state.projects.length === 0) {
    return (
      <NewProject
        first={state.projects.length === 0}
        onCreated={async (id) => {
          await refresh();
          setProjectId(id);
          setView('main');
        }}
        onCancel={state.projects.length ? () => setView('main') : undefined}
      />
    );
  }
  const current = state.projects.find((p) => p.id === projectId) ?? state.projects[state.projects.length - 1];
  if (view === 'project') {
    return (
      <ProjectSettings
        projectId={current.id}
        onBack={async () => {
          await refresh();
          setView('main');
        }}
      />
    );
  }
  return (
    <ProjectView
      key={current.id}
      project={current}
      state={state}
      onSwitch={setProjectId}
      onNew={() => setView('new')}
      onSettings={() => setView('setup')}
      onProjectSettings={() => setView('project')}
      onDeleted={async () => {
        setProjectId(null);
        await refresh();
      }}
    />
  );
}

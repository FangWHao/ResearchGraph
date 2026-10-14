import { useCallback, useEffect, useState } from 'react';
import { api } from './api';
import { Home, type Target } from './Home';
import { NewProject } from './NewProject';
import { ProjectSettings } from './ProjectSettings';
import { ProjectView } from './ProjectView';
import { Setup } from './Setup';
import type { AppState } from './types';

// 地址栏：#/ 是主页，#p=2 是项目，#p=2&e=15 停在某个事件，#p=2&t=3 停在某条研究线
type Route = { page: 'home' } | { page: 'project'; id: number; target?: Target };

function parseHash(): Route {
  const params = new URLSearchParams(window.location.hash.replace(/^#\/?/, ''));
  const id = Number(params.get('p'));
  if (!id) return { page: 'home' };
  const event = Number(params.get('e')) || undefined;
  const thread = Number(params.get('t')) || undefined;
  return { page: 'project', id, target: event || thread ? { event, thread } : undefined };
}

function toHash(route: Route): string {
  if (route.page === 'home') return '#/';
  const params = new URLSearchParams({ p: String(route.id) });
  if (route.target?.event) params.set('e', String(route.target.event));
  if (route.target?.thread) params.set('t', String(route.target.thread));
  return `#${params.toString()}`;
}

export function App() {
  const [state, setState] = useState<AppState | null>(null);
  const [view, setView] = useState<'main' | 'setup' | 'new' | 'project'>('main');
  const [route, setRoute] = useState<Route>(parseHash);
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
    const onHash = () => setRoute(parseHash());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  // 主页和项目之间来回走浏览器的历史记录，“后退”能回到主页
  const go = useCallback((next: Route) => {
    const hash = toHash(next);
    if (window.location.hash !== hash) window.location.hash = hash;
    setRoute(next);
  }, []);
  const openProject = useCallback((id: number, target?: Target) => go({ page: 'project', id, target }), [go]);

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
          openProject(id);
          setView('main');
        }}
        onCancel={state.projects.length ? () => setView('main') : undefined}
      />
    );
  }
  const current = route.page === 'project' ? state.projects.find((p) => p.id === route.id) : undefined;
  if (!current) {
    return <Home onOpen={openProject} onNew={() => setView('new')} onSettings={() => setView('setup')} />;
  }
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
      target={route.page === 'project' ? route.target : undefined}
      onHome={() => go({ page: 'home' })}
      onSwitch={(id) => openProject(id)}
      onNew={() => setView('new')}
      onSettings={() => setView('setup')}
      onProjectSettings={() => setView('project')}
      onDeleted={async () => {
        go({ page: 'home' });
        await refresh();
      }}
    />
  );
}

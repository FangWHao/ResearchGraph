import { useCallback, useEffect, useRef, useState } from 'react';
import { ManualDecisionDialog } from './ManualDecisionDialog';
import { ResolveDecisionDialog } from './ResolveDecisionDialog';
import { currentDecisionIntent, currentResolutionIntent, emptyDecisionDraft, emptyResolutionDraft } from './manualDecision';
import type { DecisionDraft, ResolutionDraft } from './manualDecision';
import type { DecisionResult, Project, ResolveDecisionResult } from './types';

type Open = { kind: 'create'; project: string } | { kind: 'resolve'; project: string; claim: number };
const resolutionKey = (project: string, claim: number) => `${project}:${claim}`;

export function useManualDecisions({ project, actor, authorized, revision, epoch, onError, onRefresh, onDataChanged, onSaved, onClaim }: {
  project: Project | undefined; actor: string; authorized: boolean; revision: number | null; epoch: number;
  onError: (error: unknown) => void; onRefresh: () => void; onDataChanged: () => void;
  onSaved: (claim: number, revision: number, message: string) => void; onClaim: (claim: number) => void;
}) {
  const [open, setOpen] = useState<Open | null>(null);
  const [decisions, setDecisions] = useState<Record<string, DecisionDraft>>({});
  const [resolutions, setResolutions] = useState<Record<string, ResolutionDraft>>({});
  const current = useRef({ project: project?.project_id, actor, authorized, generation: 0 });
  if (current.current.authorized !== authorized) current.current.generation += 1;
  Object.assign(current.current, { project: project?.project_id, actor, authorized });
  const openRef = useRef(open), decisionsRef = useRef(decisions), resolutionsRef = useRef(resolutions);
  openRef.current = open; decisionsRef.current = decisions; resolutionsRef.current = resolutions;
  const close = useCallback(() => { openRef.current = null; setOpen(null); }, []);
  useEffect(() => { close(); }, [project?.project_id, close]);
  function changeDecision(key: string, draft: DecisionDraft) {
    decisionsRef.current = { ...decisionsRef.current, [key]: draft }; setDecisions(decisionsRef.current);
  }
  function changeResolution(key: string, draft: ResolutionDraft) {
    resolutionsRef.current = { ...resolutionsRef.current, [key]: draft }; setResolutions(resolutionsRef.current);
  }
  function openCreate() {
    if (!project) return;
    if (!decisionsRef.current[project.project_id]) changeDecision(project.project_id, emptyDecisionDraft());
    openRef.current = { kind: 'create', project: project.project_id }; setOpen(openRef.current);
  }
  function openResolve(claim: number) {
    if (!project) return;
    const key = resolutionKey(project.project_id, claim);
    if (!resolutionsRef.current[key]) changeResolution(key, emptyResolutionDraft());
    openRef.current = { kind: 'resolve', project: project.project_id, claim }; setOpen(openRef.current);
  }
  const activeProject = open?.project, activeClaim = open?.kind === 'resolve' ? open.claim : null;
  const generation = current.current.generation;
  function isActive(kind: Open['kind'], savedProject: string, claim: number | null) {
    const active = openRef.current;
    return current.current.authorized && current.current.generation === generation && current.current.project === savedProject
      && active?.project === savedProject && active.kind === kind && (active.kind !== 'resolve' || active.claim === claim);
  }
  const readError = useCallback((error: unknown) => {
    const active = openRef.current;
    if (current.current.authorized && current.current.generation === generation && current.current.project === activeProject
      && active?.kind === 'resolve' && active.project === activeProject && active.claim === activeClaim) onError(error);
  }, [activeProject, activeClaim, generation, onError]);
  function created(savedProject: string, result: DecisionResult) {
    onDataChanged();
    if (current.current.generation !== generation || !current.current.authorized
      || !currentDecisionIntent(decisionsRef.current[savedProject], savedProject, current.current.actor, result.request_id)) return;
    changeDecision(savedProject, emptyDecisionDraft());
    if (isActive('create', savedProject, null)) {
      close(); onSaved(result.claim_id, result.revision, result.target_id == null ? '决定已保存，对象仍需确定' : result.replayed ? '已找回保存的人工决定' : '人工决定已保存');
    }
  }
  function resolved(savedProject: string, claim: number, result: ResolveDecisionResult) {
    onDataChanged(); const key = resolutionKey(savedProject, claim);
    if (result.original_claim_id !== claim || current.current.generation !== generation || !current.current.authorized
      || !currentResolutionIntent(resolutionsRef.current[key], savedProject, claim, current.current.actor, result.request_id)) return;
    changeResolution(key, emptyResolutionDraft());
    if (isActive('resolve', savedProject, claim)) { close(); onSaved(result.claim_id, result.revision, result.replayed ? '已找回确定对象的记录' : '对象已确定，原动作和范围已保留'); }
  }
  const visible = authorized && project != null && open?.project === project.project_id;
  const dialogs = visible && open.kind === 'create' ? <ManualDecisionDialog key={`create:${project.project_id}`} project={project}
    revision={revision} actor={actor} draft={decisions[project.project_id]} onDraft={draft => changeDecision(project.project_id, draft)}
    onClose={close} onCreated={result => created(project.project_id, result)} onRefresh={onRefresh}
    onError={(error, id) => { if (isActive('create', project.project_id, null) && currentDecisionIntent(decisionsRef.current[project.project_id], project.project_id, current.current.actor, id)) onError(error); }} />
    : visible && open.kind === 'resolve' ? <ResolveDecisionDialog key={`resolve:${project.project_id}:${open.claim}`} project={project}
      claimId={open.claim} actor={actor} epoch={epoch} draft={resolutions[resolutionKey(project.project_id, open.claim)]}
      onDraft={draft => changeResolution(resolutionKey(project.project_id, open.claim), draft)} onClose={close}
      onResolved={result => resolved(project.project_id, open.claim, result)} onRefresh={onRefresh} onReadError={readError}
      onClaim={claim => { close(); onClaim(claim); }}
      onError={(error, id) => { if (isActive('resolve', project.project_id, open.claim) && currentResolutionIntent(resolutionsRef.current[resolutionKey(project.project_id, open.claim)], project.project_id, open.claim, current.current.actor, id)) onError(error); }} /> : null;
  return { openCreate, openResolve, close, visible, dialogs };
}

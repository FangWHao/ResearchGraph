import { useEffect, useRef, useState } from 'react';
import { ManualNoteDialog } from './ManualNoteDialog';
import { emptyNoteDraft, isCurrentNoteIntent } from './manualNote';
import type { NoteDraft } from './manualNote';
import type { NoteResult, Project } from './types';

export function useManualNotes({ project, actor, authorized, revision, onError, onRefresh, onDataChanged, onSaved }: {
  project: Project | undefined; actor: string; authorized: boolean; revision: number | null;
  onError: (error: unknown) => void; onRefresh: () => void; onDataChanged: () => void;
  onSaved: (result: NoteResult) => void;
}) {
  const [drafts, setDrafts] = useState<Record<string, NoteDraft>>({}), [opened, setOpened] = useState<string | null>(null);
  const currentDrafts = useRef(drafts), current = useRef({ project, actor, authorized, opened });
  current.current = { project, actor, authorized, opened };
  function update(id: string, draft: NoteDraft) { currentDrafts.current = { ...currentDrafts.current, [id]: draft }; setDrafts(currentDrafts.current); }
  function close() { current.current.opened = null; setOpened(null); }
  useEffect(close, [project?.project_id, authorized]);
  function created(savedProject: string, result: NoteResult) {
    onDataChanged();
    if (!isCurrentNoteIntent(currentDrafts.current[savedProject], savedProject, current.current.actor, result.request_id)) return;
    update(savedProject, emptyNoteDraft());
    if (current.current.authorized && current.current.project?.project_id === savedProject && current.current.opened === savedProject) { close(); onSaved(result); }
  }
  function failure(savedProject: string, sentActor: string, error: unknown, requestId: string) {
    if (current.current.authorized && current.current.project?.project_id === savedProject && current.current.opened === savedProject
      && current.current.actor === sentActor && isCurrentNoteIntent(currentDrafts.current[savedProject], savedProject, sentActor, requestId)) onError(error);
  }
  return {
    visible: opened != null,
    close,
    open: () => { if (!project || !authorized) return; if (!currentDrafts.current[project.project_id]) update(project.project_id, emptyNoteDraft()); current.current.opened = project.project_id; setOpened(project.project_id); },
    dialogs: opened === project?.project_id && project && drafts[opened] ? <ManualNoteDialog key={opened} project={project} revision={revision} actor={actor} draft={drafts[opened]}
      onDraft={draft => update(project.project_id, draft)} onClose={close} onRefresh={onRefresh}
      onCreated={result => created(project.project_id, result)} onFailure={(error, id) => failure(project.project_id, actor, error, id)} /> : null,
  };
}

import { useEffect, useMemo, useRef, useState } from 'react';
import { audits } from '../../entities/audit/index';
import type { Group, Project, Question } from '../../entities/audit/index';
import { ApiError } from '../../shared/index';

export function replaceQuestion(questions: Question[], id: string, text: string): Question[] {
  return questions.map(question => question.id === id ? { ...question, text, origin: question.origin === 'custom' ? 'custom' : 'edited' } : question);
}
export function validateQuestions(questions: Question[]): Record<string, string> {
  const errors: Record<string, string> = {}; const texts = new Set<string>();
  for (const question of questions.filter(q => q.active)) {
    const key = question.text.normalize('NFKC').toLocaleLowerCase('ru-RU').replace(/\s+/g, ' ').trim();
    if (question.text.trim().length < 5) errors[question.id] = 'Вопрос слишком короткий.';
    else if (texts.has(key)) errors[question.id] = 'Такой вопрос уже есть.';
    texts.add(key);
  }
  if (!questions.some(q => q.active)) errors.form = 'Оставьте хотя бы один вопрос.';
  return errors;
}
export function useQuestionDraft(project: Project, onSaved: (project: Project) => void) {
  const [questions, setQuestions] = useState(project.questions);
  const [errors, setErrors] = useState<Record<string, string>>({}); const [error, setError] = useState<Error>(); const [saving, setSaving] = useState(false);
  const id = useRef(project.project_id);
  useEffect(() => { if (id.current !== project.project_id) { id.current = project.project_id; setQuestions(project.questions); } }, [project]);
  const dirty = useMemo(() => JSON.stringify(questions) !== JSON.stringify(project.questions), [questions, project.questions]);
  useEffect(() => { if (!dirty) return; const handler = (event: BeforeUnloadEvent) => event.preventDefault(); window.addEventListener('beforeunload', handler); return () => window.removeEventListener('beforeunload', handler); }, [dirty]);
  function edit(id: string, text: string) { setQuestions(previous => replaceQuestion(previous, id, text)); setErrors({}); setError(undefined); }
  function toggle(id: string) { setQuestions(previous => previous.map(q => q.id === id ? { ...q, active: !q.active } : q)); }
  function add(group: Group) { if (questions.filter(q => q.origin==='custom').length >= 10 || questions.length >= 50) return; setQuestions(previous => [...previous, { id: 'custom-' + crypto.randomUUID(), text: '', group, active: true, origin: 'custom', has_brand: false }]); }
  async function save(): Promise<boolean> {
    const invalid = validateQuestions(questions); setErrors(invalid); if (Object.keys(invalid).length) return false;
    setSaving(true); setError(undefined);
    try { const updated = await audits.save(project, questions); setQuestions(updated.questions); onSaved(updated); return true; }
    catch (problem) { if (problem instanceof ApiError) setErrors(problem.fields); setError(problem instanceof Error ? problem : new Error('Не удалось сохранить вопросы.')); return false; }
    finally { setSaving(false); }
  }
  function shortRun() { const seen = new Set<Group>(); setQuestions(previous => previous.map(q => { const active = !seen.has(q.group); seen.add(q.group); return { ...q, active }; })); }
  return { questions, dirty, errors, error, saving, edit, toggle, add, save, shortRun };
}

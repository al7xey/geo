import { useState } from 'react';
import type { FormEvent } from 'react';
import { audits } from '../../entities/audit/index';
import type { Project } from '../../entities/audit/index';
import { ErrorNote } from '../../shared/index';
export function ImportAnswers({ project, onImported }: { project: Project; onImported: (project: Project) => void }) {
  const [profile, setProfile] = useState<File>(); const [approved, setApproved] = useState<File>(); const [answers, setAnswers] = useState<File>(); const [synthetic, setSynthetic] = useState(true); const [busy, setBusy] = useState(false); const [error, setError] = useState<Error>();
  async function submit(event: FormEvent) {
    event.preventDefault(); if (!profile || !approved || !answers) return; setBusy(true); setError(undefined);
    try {
      if (profile.size + approved.size + answers.size > 20*1024*1024) throw new Error('Выберите набор файлов общим размером до 20 МБ.');
      const updated = await audits.import(project.project_id, { profile: JSON.parse(await profile.text()) as unknown, approved: JSON.parse(await approved.text()) as unknown, answers: await answers.text(), synthetic }); onImported(updated);
    } catch (problem) { setError(problem instanceof Error ? problem : new Error('Не удалось прочитать файлы.')); }
    finally { setBusy(false); }
  }
  return <details className="panel app-import"><summary>Уже есть собранные ответы?</summary><p className="app-muted app-space">Загрузите согласованные файлы шагов 3–4. Они заменят учебный сценарий; запросов к моделям не будет.</p><form className="app-form app-space" onSubmit={event => void submit(event)}>{([['Профиль компании — profile.json', '.json', setProfile], ['Утверждённые вопросы — step4_input.json', '.json', setApproved], ['Ответы — answers.jsonl', '.jsonl', setAnswers]] as const).map(([label, accept, update]) => <label key={label} className="app-file">{label}<input type="file" accept={accept} onChange={event => update(event.target.files?.[0])} required /></label>)}<label className="check"><input className="app-check" type="checkbox" checked={synthetic} onChange={event => setSynthetic(event.target.checked)} />Это учебные данные</label><ErrorNote error={error} /><button className="btn s sm" disabled={!profile || !approved || !answers || busy}>{busy ? 'Проверяем файлы…' : 'Проверить и загрузить'}</button></form></details>;
}

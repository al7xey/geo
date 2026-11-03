import { useState } from 'react';
import { audits } from '../../entities/audit/index';
import { ErrorNote, Icon } from '../../shared/index';
export function DisputeForm({ id }: { id: string }) {
  const [text, setText] = useState(''); const [busy, setBusy] = useState(false); const [saved, setSaved] = useState(false); const [error, setError] = useState<Error>();
  return <form className="rp-card rp-pad" onSubmit={event => { event.preventDefault(); setBusy(true); setError(undefined); void audits.dispute(id, text).then(() => { setSaved(true); setText(''); }).catch(setError).finally(() => setBusy(false)); }}><div className="rp-card-h"><h3>Здесь ошибка?</h3><span className="rp-sub">Сохраните замечание к разбору</span></div><textarea className="rp-ta-in" aria-label="Замечание к разбору" placeholder="Например: в ответе речь о другой компании" minLength={5} maxLength={2000} required value={text} onChange={event => { setText(event.target.value); setSaved(false); }} /><div className="rp-btn-row"><button className="rp-btn rp-btn-p" disabled={busy}>{busy ? 'Сохраняем…' : 'Сохранить замечание'}</button></div>{saved && <div className="rp-ok-msg" role="status"><Icon name="check" />Замечание сохранено локально. Разметка автоматически не изменялась.</div>}<ErrorNote error={error} /></form>;
}

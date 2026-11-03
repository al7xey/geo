import { useState } from 'react';
import type { PropsWithChildren } from 'react';

export function Icon({ name = 'arrow', size = 20 }: { name?: 'arrow' | 'copy' | 'check' | 'download' | 'share' | 'close' | 'eye'; size?: number }) {
  const paths = { arrow: 'M4 12h16m-6-6 6 6-6 6', copy: 'M9 9h11v11H9zM5 15H3V3h12v2', check: 'M5 12l4 4L20 5', download: 'M12 3v12m-5-5 5 5 5-5M5 18v3h14v-3', share: 'M12 15V3m-5 5 5-5 5 5M5 13v8h14v-8', close: 'm6 6 12 12M6 18 18 6', eye: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zm7 0a3 3 0 1 0 6 0 3 3 0 0 0-6 0' };
  return <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d={paths[name]} /></svg>;
}
export function ErrorNote({ error, retry }: { error?: Error | string; retry?: () => void }) {
  if (!error) return null;
  return <div role="alert" className="app-error">{typeof error === 'string' ? error : error.message}{retry && <button type="button" className="lnk" onClick={retry}>Повторить</button>}</div>;
}
export function Loading({ text = 'Загружаем сохранённые данные…' }: { text?: string }) { return <p role="status" className="app-loading"><span className="app-spinner" />{text}</p>; }
export function Empty({ children }: PropsWithChildren) { return <div className="rp-note">{children}</div>; }
export function CopyButton({ value, className = 'copybtn' }: { value: string; className?: string }) {
  const [copied, setCopied] = useState(false); const [failed, setFailed] = useState(false);
  async function copy() {
    try { await navigator.clipboard.writeText(value); setCopied(true); setTimeout(() => setCopied(false), 1800); }
    catch { setFailed(true); }
  }
  return <><button type="button" className={className + (copied ? ' copied' : '')} onClick={() => void copy()}><Icon name={copied ? 'check' : 'copy'} /><span>{copied ? 'Скопировано' : 'Скопировать ссылку'}</span></button>{failed && <span role="status">Выделите и скопируйте ссылку вручную.</span>}</>;
}
export function percent(value: number | null | undefined): string { return value == null ? 'Нет данных' : value.toLocaleString('ru-RU', { maximumFractionDigits: 1 }) + '%'; }
export function date(value: string): string { return new Intl.DateTimeFormat('ru-RU', { dateStyle: 'long' }).format(new Date(value)); }

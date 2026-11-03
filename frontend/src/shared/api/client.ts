export interface Session { csrf_token: string; identity_mode: 'test'; display_name: string; live_models: boolean; email_delivery: false }
export class ApiError extends Error {
  constructor(message: string, public code: string, public status: number, public fields: Record<string, string> = {}) { super(message); }
}
let csrf = '';
let boot: Promise<Session> | undefined;
export function bootstrap(force = false): Promise<Session> {
  if (force) boot = undefined;
  boot ??= fetch('/api/session', { credentials: 'same-origin', cache: 'no-store' }).then(async response => {
    if (!response.ok) throw new ApiError('Нет связи с локальным сервером.', 'connection', response.status);
    const data = await response.json() as Session;
    if (typeof data.csrf_token !== 'string' || data.identity_mode !== 'test') throw new Error('Сервер вернул неподдерживаемый формат.');
    csrf = data.csrf_token; return data;
  }).catch(error => { boot = undefined; throw error; });
  return boot;
}
export async function request<T>(path: string, body?: unknown, signal?: AbortSignal, retry = true): Promise<T> {
  if (body !== undefined && !csrf) await bootstrap();
  const response = await fetch('/api' + path, { method: body === undefined ? 'GET' : 'POST',
    credentials: 'same-origin', cache: 'no-store', signal,
    headers: body === undefined ? {} : { 'Content-Type': 'application/json', 'X-GEO-Token': csrf },
    body: body === undefined ? undefined : JSON.stringify(body) });
  const data: unknown = await response.json();
  if (!response.ok) {
    const problem = typeof data === 'object' && data !== null ? data as Record<string, unknown> : {};
    if (problem.error === 'csrf' && retry) { await bootstrap(true); return request<T>(path, body, signal, false); }
    const fields = problem.fields && typeof problem.fields === 'object' ? Object.fromEntries(Object.entries(problem.fields).filter(([, value]) => typeof value === 'string')) as Record<string, string> : {};
    throw new ApiError(typeof problem.message === 'string' ? problem.message : 'Не удалось выполнить действие.', typeof problem.error === 'string' ? problem.error : 'request', response.status, fields);
  }
  return data as T;
}

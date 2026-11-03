import { useSyncExternalStore } from 'react';
function subscribe(callback: () => void) { window.addEventListener('hashchange', callback); return () => window.removeEventListener('hashchange', callback); }
export function go(path: string) { window.location.hash = '#' + (path.startsWith('/') ? path : '/' + path); }
export function useRoute() {
  const hash = useSyncExternalStore(subscribe, () => window.location.hash || '#/', () => '#/');
  const [path = '/', query = ''] = hash.slice(1).split('?');
  return { path, search: new URLSearchParams(query) };
}

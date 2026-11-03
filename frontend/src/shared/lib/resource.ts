import { useEffect, useRef, useState } from 'react';

export function useResource<T>(load: (signal: AbortSignal) => Promise<T>, key: string, poll?: (value: T) => boolean) {
  const loader = useRef(load); loader.current = load;
  const polling = useRef(poll); polling.current = poll;
  const [data, setData] = useState<T>();
  const [error, setError] = useState<Error>();
  const [version, refresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    setData(undefined); setError(undefined);
    async function run() {
      try {
        const value = await loader.current(controller.signal);
        if (controller.signal.aborted) return;
        setData(value); setError(undefined);
        if (polling.current?.(value)) timer = setTimeout(run, 900);
      } catch (problem) { if (!controller.signal.aborted) setError(problem instanceof Error ? problem : new Error('Нет связи с сервером.')); }
    }
    void run();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [key, version]);
  return { data, setData, error, reload: () => refresh(value => value + 1) };
}

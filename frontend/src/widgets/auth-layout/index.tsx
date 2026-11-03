import type { PropsWithChildren } from 'react';
export function AuthLayout({ id, tag, title, description, error, children }: PropsWithChildren<{ id: string; tag: string; title: string; description: string; error?: boolean }>) {
  return <section className="page auth on" id={'p-' + id}><header className="top"><a className="brand" href="#/">itworx</a><span className="badge">Учебный вход</span></header><div className="stage"><div className="grid"><div className="left"><span className={'tagtop' + (error ? ' err' : '')}>{tag}</span><h1 className="h1">{title}</h1><p className="sub">{description}</p></div><div className="card">{children}</div></div></div></section>;
}

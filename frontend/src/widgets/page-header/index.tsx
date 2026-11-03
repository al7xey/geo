export function PageHeader({ light = false }: { light?: boolean }) {
  return <header className={light ? 'rp-top' : 'etop'}><div className="app-inline"><a className={light ? 'rp-brand' : 'brand'} href="#/">itworx</a><span className={light ? 'rp-badge' : 'badge'}>Учебный вход</span></div><a className={light ? 'rp-btn rp-s' : 'btn s sm'} href="#/reports">Мои отчёты</a></header>;
}

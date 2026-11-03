import { Component, useLayoutEffect } from 'react';
import type { ErrorInfo, ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { DomainPage } from '../pages/domain/index';
import { PromptsPage } from '../pages/prompts/index';
import { ConfirmPage } from '../pages/confirm/index';
import { ProgressPage } from '../pages/progress/index';
import { ReportsPage } from '../pages/reports/index';
import { ReportPage } from '../pages/report/index';
import { CalculationPage } from '../pages/calculation/index';
import { SharePage } from '../pages/share/index';
import { ShaderBackground } from '../widgets/shader-background/index';
import { useRoute } from '../shared/index';
import './styles/fonts.css';
import './styles/reference.css';
import './styles/interaction.css';

class Boundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(_error: Error, _info: ErrorInfo) { /* No personal data in console. */ }
  render() { return this.state.failed ? <main className="app-report-loading"><h1>Не удалось открыть страницу</h1><p>Сохранённые ответы остаются на компьютере.</p><button onClick={() => location.reload()}>Обновить страницу</button></main> : this.props.children; }
}
function App() {
  const { path, search } = useRoute();
  const auth = path==='/';
  const light = /\/(report|calculation|share)$/.test(path) || path.startsWith('/shared/') || path==='/reports';
  useLayoutEffect(() => { document.body.dataset.bg = auth ? 'shader' : 'none'; document.body.dataset.theme = light ? 'light' : 'dark'; }, [auth, light]);
  let page: ReactNode;
  const project = /^\/projects\/([a-f0-9]{32})\/(prompts|confirm|progress|report|calculation|share)$/.exec(path);
  const shared = /^\/shared\/([A-Za-z0-9_-]{43})(\/calculation)?$/.exec(path);
  if (path==='/') page = <DomainPage />;
  else if (path==='/reports') page = <ReportsPage />;
  else if (project) {
    const id = project[1]!;
    switch (project[2]) {
      case 'prompts': page = <PromptsPage id={id} />; break;
      case 'confirm': page = <ConfirmPage id={id} />; break;
      case 'progress': page = <ProgressPage id={id} />; break;
      case 'calculation': page = <CalculationPage id={id} group={search.get('group') ?? ''} />; break;
      case 'share': page = <SharePage id={id} />; break;
      default: page = <ReportPage id={id} section={search.get('section') ?? undefined} print={search.get('print')==='1'} />;
    }
  } else if (shared) page = shared[2] ? <CalculationPage token={shared[1]!} group={search.get('group') ?? ''} /> : <ReportPage token={shared[1]!} section={search.get('section') ?? undefined} />;
  else page = <main className="app-report-loading"><h1>Страница не найдена</h1><a href="#/">На главную</a></main>;
  return <Boundary key={path}>{auth && <ShaderBackground />}{page}</Boundary>;
}
createRoot(document.getElementById('root')!).render(<App />);

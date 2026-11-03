import { request } from '../../shared/index';
import type { Answer, Project, Question, Report, ShareSettings } from './model';
export const audits = {
  list: (signal?: AbortSignal) => request<{ projects: Project[] }>('/projects', undefined, signal),
  create: (url: string) => request<Project>('/projects', { url }),
  prepare: (id: string, description: string) => request<Project>(`/projects/${id}/prepare`, { description }),
  get: (id: string, signal?: AbortSignal) => request<Project>(`/projects/${encodeURIComponent(id)}`, undefined, signal),
  save: (project: Project, questions: Question[]) => request<Project>(`/projects/${project.project_id}/questions`, { revision: project.revision, questions }),
  run: (project: Project) => request<Project>(`/projects/${project.project_id}/run`, { revision: project.revision }),
  cancel: (id: string) => request<Project>(`/projects/${id}/cancel`, {}),
  report: (id: string, signal?: AbortSignal) => request<Report>(`/projects/${id}/report`, undefined, signal),
  answers: (id: string, group?: string, shared = false, signal?: AbortSignal) => request<{ answers: Answer[] }>(`/${shared ? 'shared' : 'projects'}/${id}/answers${group ? '?group=' + encodeURIComponent(group) : ''}`, undefined, signal),
  completion: (id: string, action_id: string, completed: boolean) => request<Project>(`/projects/${id}/completion`, { action_id, completed }),
  share: (id: string, settings: ShareSettings) => request<{ token: string }>(`/projects/${id}/share`, settings),
  revoke: (id: string) => request(`/projects/${id}/revoke-share`, {}),
  shared: (token: string, signal?: AbortSignal) => request<Report>(`/shared/${token}`, undefined, signal),
  dispute: (id: string, message: string) => request(`/projects/${id}/dispute`, { message }),
  import: (id: string, data: { profile: unknown; approved: unknown; answers: string; synthetic: boolean }) => request<Project>(`/projects/${id}/import`, data),
  download: (id: string, format: 'json' | 'csv' | 'html') => `/api/projects/${id}/export?format=${format}`,
};

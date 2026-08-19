import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { api } from '../lib/api';

function StatCard({ label, value, sub }) {
  return (
    <div className="glass-card p-5 rounded-2xl">
      <p className="text-xs text-text-muted uppercase tracking-wider">{label}</p>
      <p className="text-2xl font-bold gradient-text mt-1">{value}</p>
      {sub && <p className="text-xs text-text-muted mt-1">{sub}</p>}
    </div>
  );
}

function OverviewTab() {
  const { data, isLoading } = useQuery({
    queryKey: ['admin-overview'],
    queryFn: () => api.get('/api/admin/overview'),
  });

  if (isLoading) return <p className="text-text-muted">Loading...</p>;
  if (!data) return null;

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
      <StatCard label="Total Users" value={data.total_users} />
      <StatCard label="Total Articles" value={data.total_articles} />
      <StatCard label="Active Subs" value={data.active_subscriptions} />
      <StatCard label="Est. MRR" value={`$${(data.estimated_mrr_cents / 100).toFixed(0)}`} />
      <StatCard label="Failed" value={data.failed_count} />
      <StatCard label="Total Cost" value={`$${(data.total_cost_cents / 100).toFixed(2)}`} />
      {Object.entries(data.articles_by_state || {}).map(([state, count]) => (
        <StatCard key={state} label={state} value={count} />
      ))}
    </div>
  );
}

function UsersTab() {
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);

  const { data, isLoading } = useQuery({
    queryKey: ['admin-users', search, page],
    queryFn: () => api.get(`/api/admin/users?search=${search}&page=${page}&per_page=20`),
    keepPreviousData: true,
  });

  return (
    <div className="space-y-4">
      <input
        type="text"
        placeholder="Search by email..."
        value={search}
        onChange={(e) => { setSearch(e.target.value); setPage(1); }}
        className="w-full max-w-sm px-4 py-2 rounded-xl bg-bg-card border border-border text-text-primary placeholder:text-text-muted text-sm focus:outline-none focus:border-accent-blue"
        data-testid="admin-user-search"
      />

      {isLoading ? (
        <p className="text-text-muted">Loading...</p>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="admin-users-table">
              <thead>
                <tr className="text-left text-text-muted border-b border-border">
                  <th className="pb-2 pr-4">Email</th>
                  <th className="pb-2 pr-4">Status</th>
                  <th className="pb-2 pr-4">Articles</th>
                  <th className="pb-2 pr-4">Published</th>
                  <th className="pb-2">Joined</th>
                </tr>
              </thead>
              <tbody>
                {(data?.users || []).map((u) => (
                  <tr key={u.id} className="border-b border-border/50 text-text-secondary">
                    <td className="py-2 pr-4">{u.email}</td>
                    <td className="py-2 pr-4">
                      <span className={`px-2 py-0.5 rounded-full text-xs ${
                        u.subscription_status === 'active' ? 'bg-success/20 text-success' :
                        u.subscription_status === 'trialing' ? 'bg-accent-cyan/20 text-accent-cyan' :
                        'bg-text-muted/20 text-text-muted'
                      }`}>
                        {u.subscription_status || 'none'}
                      </span>
                    </td>
                    <td className="py-2 pr-4">{u.article_count}</td>
                    <td className="py-2 pr-4">{u.published_count}</td>
                    <td className="py-2">{u.created_at?.slice(0, 10)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data?.pages > 1 && (
            <div className="flex gap-2 mt-4">
              {Array.from({ length: data.pages }, (_, i) => (
                <button
                  key={i}
                  onClick={() => setPage(i + 1)}
                  className={`px-3 py-1 rounded-lg text-xs ${page === i + 1 ? 'bg-accent-blue text-white' : 'bg-bg-card text-text-secondary'}`}
                >
                  {i + 1}
                </button>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function ArticlesTab() {
  const [stateFilter, setStateFilter] = useState('');
  const [page, setPage] = useState(1);
  const [detailId, setDetailId] = useState(null);
  const queryClient = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ['admin-articles', stateFilter, page],
    queryFn: () => api.get(`/api/admin/articles?state=${stateFilter}&page=${page}&per_page=20`),
    keepPreviousData: true,
  });

  const { data: detail } = useQuery({
    queryKey: ['admin-article-detail', detailId],
    queryFn: () => api.get(`/api/admin/articles/${detailId}`),
    enabled: !!detailId,
  });

  const retryMut = useMutation({
    mutationFn: (id) => api.post(`/api/admin/articles/${id}/retry`),
    onSuccess: () => { toast.success('Article retried'); queryClient.invalidateQueries(['admin-articles']); },
    onError: (err) => toast.error(err.message),
  });

  const rollbackMut = useMutation({
    mutationFn: ({ id, target_state }) => api.post(`/api/admin/articles/${id}/rollback`, { target_state }),
    onSuccess: () => { toast.success('Article rolled back'); queryClient.invalidateQueries(['admin-articles']); },
    onError: (err) => toast.error(err.message),
  });

  const archiveMut = useMutation({
    mutationFn: (id) => api.post(`/api/admin/articles/${id}/archive`),
    onSuccess: () => { toast.success('Article archived'); queryClient.invalidateQueries(['admin-articles']); },
    onError: (err) => toast.error(err.message),
  });

  const states = ['', 'OUTLINING', 'DRAFTING', 'HUMANIZING', 'EDIT_REVIEW', 'MEDIA_ASSEMBLY', 'WAITING_CHECKPOINT_2', 'REVISION', 'READY_TO_PUBLISH', 'PUBLISHING', 'PUBLISHED', 'FAILED', 'ARCHIVED'];

  const handleArchive = (id) => {
    if (window.confirm('Archive this article? The user will be notified.')) {
      archiveMut.mutate(id);
    }
  };

  if (detailId && detail) {
    const a = detail.article;
    return (
      <div className="space-y-4">
        <button onClick={() => setDetailId(null)} className="text-accent-blue text-sm hover:underline">← Back to list</button>
        <div className="glass-card p-6 rounded-2xl space-y-4">
          <h3 className="text-lg font-bold text-text-primary">{a.title}</h3>
          <div className="grid grid-cols-2 gap-4 text-sm text-text-secondary">
            <div><span className="text-text-muted">State:</span> {a.state}</div>
            <div><span className="text-text-muted">User:</span> {a.user_email}</div>
            <div><span className="text-text-muted">Keyword:</span> {a.target_keyword}</div>
            <div><span className="text-text-muted">Iterations:</span> {a.lifetime_draft_iterations}</div>
            {a.failure_reason && <div className="col-span-2"><span className="text-error">Failure:</span> {a.failure_reason}</div>}
          </div>
          <div className="flex gap-2 mt-4">
            {a.state === 'FAILED' && (
              <button onClick={() => retryMut.mutate(a.id)} className="btn-primary text-xs px-3 py-1.5 rounded-lg">Retry</button>
            )}
            <select
              onChange={(e) => { if (e.target.value) rollbackMut.mutate({ id: a.id, target_state: e.target.value }); }}
              className="bg-bg-card border border-border rounded-lg text-xs px-2 py-1.5 text-text-secondary"
              defaultValue=""
              data-testid="rollback-select"
            >
              <option value="">Rollback to...</option>
              {states.filter(s => s && s !== a.state).map(s => <option key={s} value={s}>{s}</option>)}
            </select>
            {a.state !== 'ARCHIVED' && (
              <button onClick={() => handleArchive(a.id)} className="bg-error/20 text-error text-xs px-3 py-1.5 rounded-lg hover:bg-error/30">Archive</button>
            )}
          </div>
          {a.pipeline_events?.length > 0 && (
            <div className="mt-4">
              <h4 className="text-sm font-semibold text-text-primary mb-2">Pipeline Events</h4>
              <div className="space-y-1">
                {a.pipeline_events.map((e, i) => (
                  <div key={i} className="text-xs text-text-muted flex gap-2">
                    <span>{e.created_at?.slice(0, 19)}</span>
                    <span className="text-text-secondary">{e.event_type}</span>
                    {e.from_state && <span>{e.from_state} → {e.to_state}</span>}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <select
        value={stateFilter}
        onChange={(e) => { setStateFilter(e.target.value); setPage(1); }}
        className="px-4 py-2 rounded-xl bg-bg-card border border-border text-text-primary text-sm focus:outline-none focus:border-accent-blue"
        data-testid="admin-state-filter"
      >
        {states.map(s => <option key={s} value={s}>{s || 'All States'}</option>)}
      </select>

      {isLoading ? (
        <p className="text-text-muted">Loading...</p>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="admin-articles-table">
              <thead>
                <tr className="text-left text-text-muted border-b border-border">
                  <th className="pb-2 pr-4">Title</th>
                  <th className="pb-2 pr-4">State</th>
                  <th className="pb-2 pr-4">User</th>
                  <th className="pb-2 pr-4">Failure</th>
                  <th className="pb-2">Actions</th>
                </tr>
              </thead>
              <tbody>
                {(data?.articles || []).map((a) => (
                  <tr key={a.id} className="border-b border-border/50 text-text-secondary">
                    <td className="py-2 pr-4">
                      <button onClick={() => setDetailId(a.id)} className="text-accent-blue hover:underline text-left">{a.title}</button>
                    </td>
                    <td className="py-2 pr-4">
                      <span className={`px-2 py-0.5 rounded-full text-xs ${
                        a.state === 'PUBLISHED' ? 'bg-success/20 text-success' :
                        a.state === 'FAILED' ? 'bg-error/20 text-error' :
                        'bg-accent-blue/20 text-accent-blue'
                      }`}>
                        {a.state}
                      </span>
                    </td>
                    <td className="py-2 pr-4">{a.user_email}</td>
                    <td className="py-2 pr-4 text-xs text-error">{a.failure_reason || '—'}</td>
                    <td className="py-2 flex gap-1">
                      {a.state === 'FAILED' && (
                        <button onClick={() => retryMut.mutate(a.id)} className="text-xs px-2 py-1 rounded bg-accent-blue/20 text-accent-blue hover:bg-accent-blue/30">Retry</button>
                      )}
                      {a.state !== 'ARCHIVED' && (
                        <button onClick={() => handleArchive(a.id)} className="text-xs px-2 py-1 rounded bg-error/20 text-error hover:bg-error/30">Archive</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data?.pages > 1 && (
            <div className="flex gap-2 mt-4">
              {Array.from({ length: data.pages }, (_, i) => (
                <button
                  key={i}
                  onClick={() => setPage(i + 1)}
                  className={`px-3 py-1 rounded-lg text-xs ${page === i + 1 ? 'bg-accent-blue text-white' : 'bg-bg-card text-text-secondary'}`}
                >
                  {i + 1}
                </button>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

const tabs = [
  { id: 'overview', label: 'Overview' },
  { id: 'users', label: 'Users' },
  { id: 'articles', label: 'Articles' },
];

export function Admin() {
  const [tab, setTab] = useState('overview');

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold gradient-text">Admin Dashboard</h2>

      <div className="flex gap-1 bg-bg-card rounded-xl p-1 w-fit">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${
              tab === t.id
                ? 'bg-accent-blue/20 text-accent-blue'
                : 'text-text-secondary hover:text-text-primary'
            }`}
            data-testid={`admin-tab-${t.id}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div>
        {tab === 'overview' && <OverviewTab />}
        {tab === 'users' && <UsersTab />}
        {tab === 'articles' && <ArticlesTab />}
      </div>
    </div>
  );
}

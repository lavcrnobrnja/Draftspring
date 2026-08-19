import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Toaster } from 'react-hot-toast';
import { RequireAuth } from './components/RequireAuth';
import { RequireSubscription } from './components/RequireSubscription';
import { RequireReviewAuth } from './components/RequireReviewAuth';
import { DashboardLayout } from './components/DashboardLayout';
import { Login } from './pages/Login';
import { AuthVerify } from './pages/AuthVerify';
import { Subscribe } from './pages/Subscribe';
import { Runway } from './pages/Runway';
import { NewBatch } from './pages/NewBatch';
import { Settings } from './pages/Settings';
import { Vault } from './pages/Vault';
import { Usage } from './pages/Usage';
import { AdminArea } from './pages/AdminArea';
import { IdeaReview } from './pages/IdeaReview';
import { ArticleReview } from './pages/ArticleReview';
import { GhostHealthCheck } from './pages/GhostHealthCheck';
import { TryDraftSpring } from './pages/TryDraftSpring';

const qc = new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } } });

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <Routes>
          <Route path="/tools/ghost-health-check" element={<GhostHealthCheck />} />
          <Route path="/tools/try-draftspring" element={<TryDraftSpring />} />
          <Route path="/login" element={<Login />} />
          <Route path="/auth/verify" element={<AuthVerify />} />
          <Route path="/subscribe" element={<RequireAuth><Subscribe /></RequireAuth>} />
          <Route path="/dashboard" element={<RequireAuth><RequireSubscription><DashboardLayout /></RequireSubscription></RequireAuth>}>
            <Route index element={<Runway />} />
            <Route path="new-batch" element={<NewBatch />} />
            <Route path="settings" element={<Settings />} />
            <Route path="vault" element={<Vault />} />
            <Route path="usage" element={<Usage />} />
          </Route>
          <Route path="/daddyo/*" element={<AdminArea />} />
          <Route path="/review/ideas/:batchId" element={<RequireReviewAuth><IdeaReview /></RequireReviewAuth>} />
          <Route path="/review/article/:articleId" element={<RequireReviewAuth><ArticleReview /></RequireReviewAuth>} />
          <Route path="*" element={<Navigate to="/login" replace />} />
        </Routes>
      </BrowserRouter>
      <Toaster position="bottom-right" toastOptions={{ duration: 6000, style: { background: '#111827', color: '#F8FAFC', border: '1px solid rgba(148,163,184,0.08)', borderRadius: '12px', fontSize: '14px' }, success: { duration: 6000, iconTheme: { primary: '#10B981', secondary: '#111827' } }, error: { duration: 6000, iconTheme: { primary: '#EF4444', secondary: '#111827' } } }} />
    </QueryClientProvider>
  );
}

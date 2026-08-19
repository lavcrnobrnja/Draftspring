import { NavLink, useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';

const navItems = [
  { to: '/dashboard', label: 'Dashboard', icon: DashboardIcon },
  { to: '/dashboard/new-batch', label: 'New Content', icon: NewBatchIcon },
  { to: '/dashboard/vault', label: 'Image Vault', icon: VaultIcon },
  { to: '/dashboard/usage', label: 'Usage', icon: UsageIcon },
];

export function Sidebar() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const ghostConnected = user?.ghost_key_valid === 1;

  const statusColor = {
    active: 'bg-success',
    trialing: 'bg-accent-cyan',
    none: 'bg-text-muted',
    canceled: 'bg-warning',
    past_due: 'bg-error',
  };

  const statusLabel = {
    active: 'Active',
    trialing: 'Free Trial',
    none: 'No Plan',
    canceled: 'Canceled',
    past_due: 'Past Due',
  };

  const status = user?.subscription_status || 'none';

  return (
    <aside className="w-64 h-screen bg-bg-card border-r border-border flex flex-col fixed left-0 top-0">
      {/* Logo */}
      <div className="px-6 py-6 border-b border-border">
        <div className="flex items-center gap-2">
          <h1 className="text-xl font-bold gradient-text">DraftSpring</h1>
          <span className="px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider bg-accent-blue/15 text-accent-blue border border-accent-blue/30 rounded-full leading-none">beta</span>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-3 py-4 space-y-1">
        {navItems.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/dashboard'}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-all duration-200 ${
                isActive
                  ? 'bg-accent-blue/10 text-accent-blue'
                  : 'text-text-secondary hover:text-text-primary hover:bg-bg-hover'
              }`
            }
          >
            <Icon className="w-5 h-5 flex-shrink-0" />
            {label}
          </NavLink>
        ))}

        {/* Ghost connection prompt */}
        {!ghostConnected && (
          <button
            onClick={() => navigate('/dashboard/settings?tab=ghost')}
            className="mx-1 mt-4 flex items-center gap-3 px-3 py-3 rounded-xl border border-warning/30 bg-warning/5 hover:bg-warning/10 transition-all group cursor-pointer"
            data-testid="ghost-connect-prompt"
          >
            <span className="relative flex-shrink-0">
              <GhostIcon className="w-5 h-5 text-warning" />
              <span className="absolute -top-0.5 -right-0.5 w-2.5 h-2.5 rounded-full bg-warning animate-pulse" />
            </span>
            <div className="text-left">
              <p className="text-xs font-semibold text-warning">Connect Ghost</p>
              <p className="text-[10px] text-text-muted leading-tight">Required to publish</p>
            </div>
          </button>
        )}
      </nav>

      {/* Footer */}
      <div className="px-3 py-3 border-t border-border">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="relative flex-shrink-0">
              <div className="w-8 h-8 rounded-lg bg-bg-hover flex items-center justify-center text-xs font-semibold text-text-secondary uppercase">
                {user?.email?.[0] || '?'}
              </div>
              <div className={`absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full border-2 border-bg-card ${statusColor[status] || 'bg-text-muted'}`} />
            </div>
            <div className="min-w-0">
              <p className="text-xs font-medium text-text-primary truncate">{user?.email || ''}</p>
              <p className="text-[10px] text-text-muted">{statusLabel[status] || status}</p>
            </div>
          </div>
          <NavLink
            to="/dashboard/settings"
            className={({ isActive }) =>
              `p-1.5 rounded-lg transition-all duration-200 flex-shrink-0 ${
                isActive
                  ? 'text-accent-blue bg-accent-blue/10'
                  : 'text-text-muted hover:text-text-secondary hover:bg-bg-hover'
              }`
            }
            title="Settings"
          >
            <SettingsIcon className="w-4 h-4" />
          </NavLink>
        </div>
      </div>
    </aside>
  );
}

/* ── Icons ── */
function DashboardIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="7" rx="1" />
      <rect x="14" y="3" width="7" height="7" rx="1" />
      <rect x="3" y="14" width="7" height="7" rx="1" />
      <rect x="14" y="14" width="7" height="7" rx="1" />
    </svg>
  );
}

function NewBatchIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

function SettingsIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12.22 2h-.44a2 2 0 00-2 2v.18a2 2 0 01-1 1.73l-.43.25a2 2 0 01-2 0l-.15-.08a2 2 0 00-2.73.73l-.22.38a2 2 0 00.73 2.73l.15.1a2 2 0 011 1.72v.51a2 2 0 01-1 1.74l-.15.09a2 2 0 00-.73 2.73l.22.38a2 2 0 002.73.73l.15-.08a2 2 0 012 0l.43.25a2 2 0 011 1.73V20a2 2 0 002 2h.44a2 2 0 002-2v-.18a2 2 0 011-1.73l.43-.25a2 2 0 012 0l.15.08a2 2 0 002.73-.73l.22-.39a2 2 0 00-.73-2.73l-.15-.08a2 2 0 01-1-1.74v-.5a2 2 0 011-1.74l.15-.09a2 2 0 00.73-2.73l-.22-.38a2 2 0 00-2.73-.73l-.15.08a2 2 0 01-2 0l-.43-.25a2 2 0 01-1-1.73V4a2 2 0 00-2-2z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function VaultIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <circle cx="12" cy="12" r="4" />
      <path d="M12 8v8M8 12h8" />
    </svg>
  );
}

function UsageIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M18 20V10M12 20V4M6 20v-6" />
    </svg>
  );
}

function GhostIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2C6.48 2 2 6.48 2 12v8a2 2 0 002 2h1a1 1 0 001-1v-1a1 1 0 012 0v1a1 1 0 001 1h2a1 1 0 001-1v-1a1 1 0 012 0v1a1 1 0 001 1h2a1 1 0 001-1v-1a1 1 0 012 0v1a1 1 0 001 1h1a2 2 0 002-2v-8c0-5.52-4.48-10-10-10z" />
      <circle cx="9" cy="12" r="1.5" fill="currentColor" />
      <circle cx="15" cy="12" r="1.5" fill="currentColor" />
    </svg>
  );
}

function AdminIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
    </svg>
  );
}

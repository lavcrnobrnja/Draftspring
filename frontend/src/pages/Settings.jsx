import { useState, useCallback, useEffect } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';
import { api } from '../lib/api';
import { trackEvent } from '../lib/posthog';
import toast from 'react-hot-toast';
import { DEFAULT_IMAGE_STYLE, DEFAULT_IMAGE_SUBSTYLE, IMAGE_STYLE_OPTIONS, getSubstyles, firstSubstyle } from '../lib/imageStyles';

const STATUS_CONFIG = {
  active: { label: 'Active', color: 'success', desc: 'Your subscription is active.' },
  trialing: { label: 'Free Trial', color: 'accent-cyan', desc: 'You\'re on a free trial.' },
  none: { label: 'No Plan', color: 'text-muted', desc: 'You don\'t have an active subscription.' },
  canceled: { label: 'Canceled', color: 'warning', desc: 'Your subscription has been canceled.' },
  past_due: { label: 'Past Due', color: 'error', desc: 'Your payment failed. Please update your payment method.' },
};

const TABS = ['profile', 'ghost', 'schedule', 'billing'];
const DAYS = ['monday','tuesday','wednesday','thursday','friday','saturday','sunday'];
const DS = {monday:'Mon',tuesday:'Tue',wednesday:'Wed',thursday:'Thu',friday:'Fri',saturday:'Sat',sunday:'Sun'};
const TZS = ['America/New_York','America/Chicago','America/Denver','America/Los_Angeles','America/Toronto','Europe/London','Europe/Berlin','Europe/Paris','Asia/Tokyo','Asia/Singapore','Australia/Sydney','UTC'];
const TIMES = Array.from({length:24},(_,h)=>`${String(h).padStart(2,'0')}:00`);
const WCS = [800,1000,1200,1500,2000,2500,3000];

export function Settings() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [searchParams] = useSearchParams();
  const [tab, setTab] = useState(() => {
    const t = searchParams.get('tab');
    return TABS.includes(t) ? t : 'profile';
  });

  return (
    <div className="max-w-2xl">
      <div className="mb-8"><h1 className="text-2xl font-bold text-text-primary">Settings</h1><p className="text-text-secondary text-sm mt-1">Configure your setup</p></div>
      <div className="flex gap-1 mb-6 p-1 bg-bg-card rounded-xl border border-border w-fit">
        {TABS.map(t => <button key={t} onClick={() => setTab(t)} className={`px-4 py-2 text-sm font-medium rounded-lg capitalize transition-all ${tab===t?'bg-accent-blue/20 text-accent-blue':'text-text-secondary hover:text-text-primary'}`}>{t}</button>)}
      </div>
      {tab === 'ghost' && <GhostTab user={user} qc={qc} />}
      {tab === 'schedule' && <ScheduleTab user={user} qc={qc} />}
      {tab === 'profile' && <ProfileTab key={user?.id || 'profile-loading'} user={user} qc={qc} />}
      {tab === 'billing' && <BillingTab user={user} />}
    </div>
  );
}

function GhostSetupModal({ onClose }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-bg-card border border-border rounded-2xl shadow-2xl max-w-lg w-full mx-4 max-h-[80vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-6 py-4 border-b border-border">
          <h3 className="text-lg font-bold text-text-primary">How to get your Ghost Admin API Key</h3>
          <button onClick={onClose} className="text-text-muted hover:text-text-primary text-xl leading-none">&times;</button>
        </div>
        <div className="px-6 py-5 space-y-5 text-sm text-text-secondary">
          <div>
            <p className="font-semibold text-text-primary mb-2">Step 1 — Open Ghost Admin</p>
            <p>Go to your Ghost blog and add <code className="text-accent-blue bg-bg-deep px-1.5 py-0.5 rounded text-xs">/ghost/</code> to the URL.</p>
            <p className="text-xs text-text-muted mt-1">Example: <code className="text-accent-blue bg-bg-deep px-1.5 py-0.5 rounded text-xs">https://your-blog.com/ghost/</code></p>
          </div>

          <div>
            <p className="font-semibold text-text-primary mb-2">Step 2 — Navigate to Integrations</p>
            <p>In the left sidebar, click <strong className="text-text-primary">Settings</strong>, then scroll down to the <strong className="text-text-primary">Advanced</strong> section and click <strong className="text-text-primary">Integrations</strong>.</p>
          </div>

          <div>
            <p className="font-semibold text-text-primary mb-2">Step 3 — Create a Custom Integration</p>
            <p>Click <strong className="text-text-primary">"Add custom integration"</strong> at the bottom of the page.</p>
            <p className="mt-1">Name it something like <code className="text-accent-blue bg-bg-deep px-1.5 py-0.5 rounded text-xs">DraftSpring</code> and click <strong className="text-text-primary">Create</strong>.</p>
          </div>

          <div>
            <p className="font-semibold text-text-primary mb-2">Step 4 — Copy the Admin API Key</p>
            <p>On the integration page, you&apos;ll see:</p>
            <ul className="list-disc list-inside mt-2 space-y-1 text-text-muted">
              <li><strong className="text-text-secondary">Content API Key</strong> — you do NOT need this one</li>
              <li><strong className="text-text-secondary">Admin API Key</strong> — copy this one ✓</li>
            </ul>
            <p className="mt-2">The Admin API Key looks like this:</p>
            <code className="block mt-1 text-xs text-accent-cyan bg-bg-deep px-3 py-2 rounded-lg break-all">64f8c4e99a0b12345abcdef0:a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2</code>
            <p className="text-xs text-text-muted mt-1">It&apos;s two parts separated by a colon — copy the entire thing.</p>
          </div>

          <div>
            <p className="font-semibold text-text-primary mb-2">Step 5 — Paste it here</p>
            <p>Enter your Ghost blog URL and paste the Admin API Key above, then click <strong className="text-text-primary">Connect Ghost</strong>. We&apos;ll verify the connection automatically.</p>
          </div>

          <div className="p-3 rounded-xl bg-accent-blue/5 border border-accent-blue/20">
            <p className="text-xs text-accent-blue"><strong>🔒 Your key is encrypted</strong> — we store it with AES-256 encryption and never expose it in the UI.</p>
          </div>
        </div>
        <div className="px-6 py-4 border-t border-border">
          <button onClick={onClose} className="btn-primary w-full">Got it</button>
        </div>
      </div>
    </div>
  );
}

function GhostAuthorPicker({ staff, currentAuthorId, qc }) {
  const [selected, setSelected] = useState(currentAuthorId || '');
  const authorMut = useMutation({
    mutationFn: b => api.put('/api/settings/ghost/author', b),
    onSuccess: d => { toast.success(d.ghost_author_name ? `Publishing as ${d.ghost_author_name}` : 'Reset to default author'); qc.invalidateQueries({queryKey:['settings']}); },
  });

  const handleChange = (e) => {
    const id = e.target.value;
    setSelected(id);
    if (id) {
      const member = staff.find(s => s.id === id);
      if (member) {
        authorMut.mutate({ ghost_author_id: member.id, ghost_author_name: member.name });
      }
    } else {
      // Clear author — revert to Ghost default (blog owner)
      authorMut.mutate({ ghost_author_id: null, ghost_author_name: null });
    }
  };

  return (
    <div>
      <label className="block text-sm font-medium text-text-secondary mb-1.5">Publish as</label>
      <select
        value={selected}
        onChange={handleChange}
        disabled={authorMut.isPending}
        className="w-full px-4 py-3 rounded-xl bg-bg-deep border border-border text-text-primary text-sm focus:outline-none focus:border-accent-blue/50 focus:ring-1 focus:ring-accent-blue/30 transition-all"
        data-testid="ghost-author-select"
      >
        <option value="">Default (blog owner)</option>
        {staff.map(s => (
          <option key={s.id} value={s.id}>{s.name} ({s.role})</option>
        ))}
      </select>
      <p className="text-xs text-text-muted mt-1">Choose which staff member articles are published under</p>
    </div>
  );
}

function GhostTab({ user, qc }) {
  const ok = user?.ghost_key_valid === 1;
  const [url, setUrl] = useState(ok ? (user?.ghost_url || '') : '');
  const [key, setKey] = useState('');
  const [showHelp, setShowHelp] = useState(false);
  const [staff, setStaff] = useState(null);
  const [loadingStaff, setLoadingStaff] = useState(false);
  const [staffError, setStaffError] = useState(false);
  const [staffErrorMsg, setStaffErrorMsg] = useState('');

  const mut = useMutation({
    mutationFn: b => api.put('/api/settings/ghost', b),
    onSuccess: d => {
      if (d.valid === false) {
        toast.error(d.error || 'Could not verify Ghost connection');
      } else {
        trackEvent('ghost_connected', { site_title: d.site_title });
        toast.success(`Connected to ${d.site_title||'Ghost'}`);
        setStaffError(false);
        setStaffErrorMsg('');
        if (d.staff) { setStaff(d.staff); setStaffError(d.staff.length === 0); }
      }
      qc.invalidateQueries({queryKey:['settings']});
      qc.invalidateQueries({queryKey:['auth']});
    },
  });

  // Load staff list when already connected but no staff loaded yet
  const fetchStaff = useCallback(async () => {
    setLoadingStaff(true);
    setStaffError(false);
    setStaffErrorMsg('');
    try {
      const res = await api.get('/api/settings/ghost/staff');
      if (res.error) {
        // API key is bad — backend already set ghost_key_valid=0
        setStaffError(true);
        setStaffErrorMsg(res.error);
        setStaff([]);
        // Refetch user data so the UI reflects disconnected state
        qc.invalidateQueries({queryKey:['settings']});
        qc.invalidateQueries({queryKey:['auth']});
      } else {
        setStaff(res.staff || []);
        if (!res.staff || res.staff.length === 0) setStaffError(true);
      }
    } catch {
      setStaffError(true);
      setStaffErrorMsg('Could not reach server');
      setStaff([]);
    }
    setLoadingStaff(false);
  }, [qc]);

  useEffect(() => {
    if (!ok || staff) return;
    const timer = setTimeout(() => { fetchStaff(); }, 0);
    return () => clearTimeout(timer);
  }, [ok, staff, fetchStaff]);

  return (
    <div className="glass-card p-6 space-y-6">
      {ok && <div className="flex items-center gap-3 p-3 rounded-xl bg-success/10 border border-success/20"><div className="w-2.5 h-2.5 rounded-full bg-success" /><div><p className="text-sm font-medium text-success">Connected</p><p className="text-xs text-text-muted">{user?.ghost_site_title||user?.ghost_url} {'\u00B7'} Ghost {user?.ghost_version}</p></div></div>}
      <div><label className="block text-sm font-medium text-text-secondary mb-1.5">Ghost URL</label><input type="url" value={url} onChange={e => setUrl(e.target.value)} placeholder="https://your-blog.ghost.io" className="w-full px-4 py-3 rounded-xl bg-bg-deep border border-border text-text-primary text-sm placeholder:text-text-muted focus:outline-none focus:border-accent-blue/50 focus:ring-1 focus:ring-accent-blue/30 transition-all" /></div>
      <div>
        <div className="flex items-center gap-2 mb-1.5">
          <label className="block text-sm font-medium text-text-secondary">Admin API Key</label>
          <button
            onClick={() => setShowHelp(true)}
            className="w-5 h-5 rounded-full bg-bg-deep border border-border text-text-muted hover:text-accent-blue hover:border-accent-blue/50 flex items-center justify-center text-xs font-bold transition-all"
            title="How to get your API key"
            data-testid="ghost-help-button"
          >
            i
          </button>
        </div>
        <input type="password" value={key} onChange={e => setKey(e.target.value)} placeholder="Ghost Admin API key" className="w-full px-4 py-3 rounded-xl bg-bg-deep border border-border text-text-primary text-sm placeholder:text-text-muted focus:outline-none focus:border-accent-blue/50 focus:ring-1 focus:ring-accent-blue/30 transition-all" />
        <p className="text-xs text-text-muted mt-1">Ghost → Settings → Integrations → Custom integration</p>
      </div>
      {ok && loadingStaff && <p className="text-xs text-text-muted">Loading staff members…</p>}
      {ok && staff && staff.length > 0 && <GhostAuthorPicker staff={staff} currentAuthorId={user?.ghost_author_id} qc={qc} />}
      {staffError && !loadingStaff && <p className="text-xs text-amber-400">{staffErrorMsg || 'Could not load staff list — try reconnecting Ghost'}</p>}
      <button onClick={() => mut.mutate({ghost_url:url,ghost_admin_api_key:key})} disabled={!url.trim()||!key.trim()||mut.isPending} className="btn-primary">{mut.isPending ? 'Validating...' : ok ? 'Reconnect' : 'Connect Ghost'}</button>
      {showHelp && <GhostSetupModal onClose={() => setShowHelp(false)} />}
    </div>
  );
}

function ScheduleTab({ user, qc }) {
  const navigate = useNavigate();
  const [days, setDays] = useState(() => { try { return JSON.parse(user?.publish_days||'[]'); } catch { return []; } });
  const [time, setTime] = useState(user?.publish_time || '09:00');
  const [tz, setTz] = useState(user?.publish_timezone || 'America/New_York');
  const mut = useMutation({ mutationFn: b => api.put('/api/settings/schedule', b), onSuccess: () => { toast.success('Schedule updated.'); qc.invalidateQueries({queryKey:['settings']}); navigate('/dashboard'); } });
  const tog = d => { if (days.includes(d)) { setDays(days.filter(x=>x!==d)); } else if (days.length<2) setDays([...days,d]); else toast.error('Max 2 days'); };
  return (
    <div className="glass-card p-6 space-y-6">
      <div><label className="block text-sm font-medium text-text-secondary mb-3">Publish on which days? Maximum 2.</label><div className="flex flex-wrap gap-2">{DAYS.map(d => <button key={d} onClick={()=>tog(d)} className={`px-4 py-2 rounded-xl text-sm font-medium transition-all ${days.includes(d)?'gradient-bg text-white shadow-lg shadow-accent-blue/20':'bg-bg-deep border border-border text-text-secondary hover:border-border-hover'}`}>{DS[d]}</button>)}</div></div>
      <div className="grid grid-cols-2 gap-4">
        <div><label className="block text-sm font-medium text-text-secondary mb-1.5">At what time?</label><select value={time} onChange={e=>setTime(e.target.value)} className="w-full px-4 py-3 rounded-xl bg-bg-deep border border-border text-text-primary text-sm focus:outline-none focus:border-accent-blue/50 transition-all">{TIMES.map(t=><option key={t} value={t}>{t}</option>)}</select></div>
        <div><label className="block text-sm font-medium text-text-secondary mb-1.5">According to which timezone?</label><select value={tz} onChange={e=>setTz(e.target.value)} className="w-full px-4 py-3 rounded-xl bg-bg-deep border border-border text-text-primary text-sm focus:outline-none focus:border-accent-blue/50 transition-all">{TZS.map(t=><option key={t} value={t}>{t.replace(/_/g,' ')}</option>)}</select></div>
      </div>
      <button onClick={()=>mut.mutate({publish_days:JSON.stringify(days),publish_time:time,publish_timezone:tz})} disabled={mut.isPending} className="btn-primary">{mut.isPending?'Saving...':'Save schedule'}</button>
    </div>
  );
}

function ProfileTab({ user, qc }) {
  const [voice, setVoice] = useState(user?.brand_voice || '');
  const [wc, setWc] = useState(user?.default_word_count || 1500);
  const [imageStyle, setImageStyle] = useState(user?.image_style || DEFAULT_IMAGE_STYLE);
  const [imageSubstyle, setImageSubstyle] = useState(user?.image_substyle || DEFAULT_IMAGE_SUBSTYLE);
  const substyles = getSubstyles(imageStyle);
  const handleImageStyleChange = (value) => {
    setImageStyle(value);
    setImageSubstyle(firstSubstyle(value));
  };
  const mut = useMutation({ mutationFn: b => api.put('/api/settings/profile', b), onSuccess: () => { toast.success('Profile updated'); qc.invalidateQueries({queryKey:['settings']}); } });
  return (
    <div className="glass-card p-6 space-y-6">
      <div><label className="block text-sm font-medium text-text-secondary mb-1.5">Email</label><input type="email" value={user?.email || ''} readOnly disabled className="w-full px-4 py-3 rounded-xl bg-bg-deep/50 border border-border text-text-muted text-sm cursor-not-allowed" /><p className="text-xs text-text-muted mt-1">Account email address.</p></div>
      <div><label className="block text-sm font-medium text-text-secondary mb-1.5">Brand voice</label><textarea value={voice} onChange={e=>setVoice(e.target.value)} placeholder="Describe your writing style..." rows={4} className="w-full px-4 py-3 rounded-xl bg-bg-deep border border-border text-text-primary text-sm placeholder:text-text-muted focus:outline-none focus:border-accent-blue/50 focus:ring-1 focus:ring-accent-blue/30 transition-all resize-none" /><p className="text-xs text-text-muted mt-1">Guides AI writing style.</p></div>
      <div><label className="block text-sm font-medium text-text-secondary mb-1.5">Word count</label><select value={wc} onChange={e=>setWc(Number(e.target.value))} className="w-full px-4 py-3 rounded-xl bg-bg-deep border border-border text-text-primary text-sm focus:outline-none focus:border-accent-blue/50 transition-all">{WCS.map(w=><option key={w} value={w}>{w.toLocaleString()} words</option>)}</select></div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div><label className="block text-sm font-medium text-text-secondary mb-1.5">Default image style</label><select value={imageStyle} onChange={e=>handleImageStyleChange(e.target.value)} className="w-full px-4 py-3 rounded-xl bg-bg-deep border border-border text-text-primary text-sm focus:outline-none focus:border-accent-blue/50 transition-all" data-testid="image-style-select">{IMAGE_STYLE_OPTIONS.map(s=><option key={s.value} value={s.value}>{s.label}</option>)}</select></div>
        <div><label className="block text-sm font-medium text-text-secondary mb-1.5">Sub-style</label><select value={imageSubstyle} onChange={e=>setImageSubstyle(e.target.value)} className="w-full px-4 py-3 rounded-xl bg-bg-deep border border-border text-text-primary text-sm focus:outline-none focus:border-accent-blue/50 transition-all" data-testid="image-substyle-select">{substyles.map(s=><option key={s.value} value={s.value}>{s.label}</option>)}</select></div>
      </div>
      <p className="text-xs text-text-muted -mt-3">Applies to newly generated article images from this point forward. Content Brief can override this for a specific brief.</p>
      <button onClick={()=>mut.mutate({brand_voice:voice,default_word_count:wc,image_style:imageStyle,image_substyle:imageSubstyle})} disabled={mut.isPending} className="btn-primary">{mut.isPending?'Saving...':'Save profile'}</button>
    </div>
  );
}

function BillingTab({ user }) {
  const [loading, setLoading] = useState(false);
  const status = user?.subscription_status || 'none';
  const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.none;
  const hasSubscription = ['active', 'trialing', 'past_due'].includes(status);

  const handleCheckout = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get('/api/checkout/session');
      if (res.url) window.location.href = res.url;
    } catch {
      toast.error('Could not start checkout. Please try again.');
    } finally {
      setLoading(false);
    }
  }, []);

  const handlePortal = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.post('/api/billing/portal');
      if (res.url) window.location.href = res.url;
    } catch {
      toast.error('Could not open billing portal. Please try again.');
    } finally {
      setLoading(false);
    }
  }, []);

  return (
    <div className="space-y-6">
      {/* Current Plan */}
      <div className="glass-card p-6">
        <h3 className="text-sm font-semibold text-text-primary mb-4">Current Plan</h3>
        <div className="flex items-center gap-3 mb-4">
          <div className={`w-3 h-3 rounded-full bg-${cfg.color}`} />
          <span className={`text-sm font-semibold text-${cfg.color}`}>{cfg.label}</span>
        </div>
        <p className="text-sm text-text-secondary mb-4">{cfg.desc}</p>
        {hasSubscription && (
          <div className="p-4 rounded-xl bg-bg-deep border border-border space-y-2">
            <div className="flex justify-between text-sm">
              <span className="text-text-muted">Plan</span>
              <span className="text-text-primary font-medium">DraftSpring</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-text-muted">Price</span>
              <span className="text-text-primary font-medium">$9/month</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-text-muted">Articles</span>
              <span className="text-text-primary font-medium">8 per billing cycle</span>
            </div>
            {user?.created_at && (
              <div className="flex justify-between text-sm">
                <span className="text-text-muted">Trial Started</span>
                <span className="text-text-primary font-medium">
                  {new Date(user.created_at.endsWith('Z') ? user.created_at : user.created_at + 'Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
                </span>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Actions */}
      {hasSubscription ? (
        <div className="glass-card p-6">
          <h3 className="text-sm font-semibold text-text-primary mb-2">Manage Subscription</h3>
          <p className="text-sm text-text-secondary mb-4">
            View invoices, update your payment method, or cancel your subscription through the Stripe customer portal.
          </p>
          <button
            onClick={handlePortal}
            disabled={loading}
            className="btn-primary"
          >
            {loading ? 'Opening...' : 'Manage Billing →'}
          </button>
        </div>
      ) : (
        <div className="glass-card p-6">
          <h3 className="text-sm font-semibold text-text-primary mb-2">Get Started</h3>
          <p className="text-sm text-text-secondary mb-1">
            Start with a <span className="font-semibold text-accent-cyan">7-day free trial</span>. No charge until the trial ends.
          </p>
          <p className="text-xs text-text-muted mb-4">$9/month after trial · 8 articles per billing cycle · Cancel anytime</p>
          <button
            onClick={handleCheckout}
            disabled={loading}
            className="btn-primary"
          >
            {loading ? 'Redirecting...' : 'Start Free Trial'}
          </button>
        </div>
      )}

      {/* Canceled — resubscribe option */}
      {status === 'canceled' && (
        <div className="glass-card p-6 border-warning/20">
          <p className="text-sm text-text-secondary mb-3">Want to come back? Start a new subscription.</p>
          <button onClick={handleCheckout} disabled={loading} className="btn-primary">
            {loading ? 'Redirecting...' : 'Resubscribe'}
          </button>
        </div>
      )}
    </div>
  );
}

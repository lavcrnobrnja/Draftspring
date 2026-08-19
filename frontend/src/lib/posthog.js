import posthog from 'posthog-js';

const POSTHOG_KEY = import.meta.env.VITE_POSTHOG_KEY;
const POSTHOG_HOST = import.meta.env.VITE_POSTHOG_HOST || 'https://us.i.posthog.com';

let initialized = false;

export function initPostHog() {
  if (initialized || !POSTHOG_KEY) {
    if (!POSTHOG_KEY) {
      console.warn('[PostHog] No API key found (VITE_POSTHOG_KEY). Analytics disabled.');
    }
    return null;
  }

  posthog.init(POSTHOG_KEY, {
    api_host: POSTHOG_HOST,
    ui_host: 'https://us.posthog.com',
    person_profiles: 'identified_only',

    // Pageview tracking — 'history_change' listens to browser history API
    // which is required for React Router SPA navigation tracking
    capture_pageview: 'history_change',
    capture_pageleave: true,

    // Autocapture — captures clicks, inputs, form submissions automatically
    autocapture: true,

    // Dead click detection — finds non-responsive UI elements
    capture_dead_clicks: true,

    // Session Replay
    disable_session_recording: false,
    enable_recording_console_log: true,

    // Heatmaps
    enable_heatmaps: true,

    // Use latest PostHog defaults for best practices
    defaults: '2026-01-30',

    // Cross-subdomain cookies for draftspring.io <-> app.draftspring.io
    cross_subdomain_cookie: true,

    // Session replay config
    session_recording: {
      // Record network requests (headers/bodies auto-redacted for sensitive data)
      recordHeaders: true,
      recordBody: false, // Don't record request bodies — could contain user content
    },
  });

  initialized = true;
  return posthog;
}

export function identifyUser(userId, properties = {}) {
  if (!initialized) return;
  posthog.identify(String(userId), properties, {
    // $set_once — only written on first identification, never overwritten
    first_seen_at: new Date().toISOString(),
  });
}

export function resetUser() {
  if (!initialized) return;
  posthog.reset();
}

export function trackEvent(eventName, properties = {}) {
  if (!initialized) return;
  posthog.capture(eventName, properties);
}

export function captureError(error, properties = {}) {
  if (!initialized) return;
  posthog.captureException(error, properties);
}

// Re-export posthog instance for PostHogProvider
export { posthog };

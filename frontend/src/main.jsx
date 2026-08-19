import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { PostHogProvider } from 'posthog-js/react';
import { initPostHog, posthog } from './lib/posthog';
import './index.css';
import App from './App.jsx';

// Initialize PostHog before rendering
initPostHog();

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <PostHogProvider client={posthog}>
      <App />
    </PostHogProvider>
  </StrictMode>,
);

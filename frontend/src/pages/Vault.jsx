import { useState, useEffect, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';

function Lightbox({ src, alt, onClose }) {
  const handleKeyDown = useCallback((e) => {
    if (e.key === 'Escape') onClose();
  }, [onClose]);

  useEffect(() => {
    document.addEventListener('keydown', handleKeyDown);
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      document.body.style.overflow = '';
    };
  }, [handleKeyDown]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: 'rgba(0, 0, 0, 0.85)' }}
      onClick={onClose}
    >
      <button
        onClick={onClose}
        className="absolute top-4 right-4 w-10 h-10 flex items-center justify-center rounded-full bg-white/10 text-white hover:bg-white/20 transition-colors text-xl"
        aria-label="Close"
      >
        ✕
      </button>
      <img
        src={src}
        alt={alt || 'Image preview'}
        className="max-w-[90vw] max-h-[90vh] object-contain rounded-lg"
        onClick={(e) => e.stopPropagation()}
      />
    </div>
  );
}

function timeAgo(dateStr) {
  if (!dateStr) return '';
  const now = new Date();
  const date = new Date(dateStr.endsWith('Z') ? dateStr : dateStr + 'Z');
  const diffMs = now - date;
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return 'just now';
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 30) return `${diffDay}d ago`;
  const diffMo = Math.floor(diffDay / 30);
  return `${diffMo}mo ago`;
}

function SourceBadge({ type }) {
  const styles = {
    seed: 'bg-accent-cyan/15 text-accent-cyan',
    vault: 'bg-accent-purple/15 text-accent-purple',
    generated: 'bg-success/15 text-success',
  };
  const labels = { seed: 'Seed', vault: 'Vault', generated: 'Generated' };
  return (
    <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded-full ${styles[type] || 'bg-bg-hover text-text-muted'}`}>
      {labels[type] || type}
    </span>
  );
}

function StateBadge({ state, label }) {
  const colors = {
    OUTLINING: 'bg-accent-cyan/15 text-accent-cyan',
    DRAFTING: 'bg-accent-blue/15 text-accent-blue',
    HUMANIZING: 'bg-accent-purple/15 text-accent-purple',
    EDIT_REVIEW: 'bg-warning/15 text-warning',
    MEDIA_ASSEMBLY: 'bg-success/15 text-success',
    WAITING_CHECKPOINT_2: 'bg-warning/15 text-warning',
    REVISION: 'bg-error/15 text-error',
    READY_TO_PUBLISH: 'bg-success/15 text-success',
    PUBLISHING: 'bg-success/15 text-success',
  };
  return (
    <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full ${colors[state] || 'bg-bg-hover text-text-muted'}`}>
      {label || state}
    </span>
  );
}

function ImageCard({ image, context, onImageClick }) {
  const [loadError, setLoadError] = useState(false);
  return (
    <div className="group relative">
      <div
        className="w-24 h-24 rounded-lg overflow-hidden bg-bg-deep border border-border/50 cursor-pointer hover:border-accent-blue/40 transition-colors"
        onClick={() => !loadError && onImageClick && onImageClick(image.storage_url, image.alt_text || image.filename || 'Image')}
      >
        {loadError ? (
          <div className="w-full h-full flex items-center justify-center text-text-muted text-xs">No preview</div>
        ) : (
          <img
            src={image.storage_url}
            alt={image.alt_text || image.filename || 'Image'}
            className="w-full h-full object-cover"
            onError={() => setLoadError(true)}
          />
        )}
      </div>
      <div className="mt-1.5 space-y-0.5">
        {image.source_type && <SourceBadge type={image.source_type} />}
        <p className="text-[10px] text-text-muted">{timeAgo(image.created_at)}</p>
        {image.section_heading && (
          <p className="text-[10px] text-text-muted truncate max-w-[96px]" title={image.section_heading}>{image.section_heading}</p>
        )}
        {context && (
          <p className="text-[10px] text-text-muted truncate max-w-[96px]" title={context}>{context}</p>
        )}
      </div>
    </div>
  );
}

function TabBar({ tabs, activeTab, onChange }) {
  return (
    <div className="flex border-b border-border mb-6">
      {tabs.map((tab) => (
        <button
          key={tab.key}
          type="button"
          onClick={() => onChange(tab.key)}
          className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px ${
            activeTab === tab.key
              ? 'border-accent-blue text-accent-blue'
              : 'border-transparent text-text-muted hover:text-text-secondary'
          }`}
        >
          <span>{tab.emoji}</span>
          <span>{tab.label}</span>
          <span className={`text-xs px-1.5 py-0.5 rounded-full ${
            activeTab === tab.key ? 'bg-accent-blue/15 text-accent-blue' : 'bg-bg-hover text-text-muted'
          }`}>{tab.count}</span>
        </button>
      ))}
    </div>
  );
}

function EmptyState({ message }) {
  return (
    <div className="text-center py-8 text-text-muted text-sm">
      {message}
    </div>
  );
}

export function Vault() {
  const [activeTab, setActiveTab] = useState('published');
  const [lightbox, setLightbox] = useState(null);
  const openLightbox = (src, alt) => setLightbox({ src, alt });
  const { data, isLoading } = useQuery({
    queryKey: ['vault-gallery'],
    queryFn: () => api.get('/api/vault/gallery'),
  });

  const published = data?.published || [];
  const inProgress = data?.in_progress || [];
  const available = data?.available || [];

  const publishedImageCount = published.reduce((sum, g) => sum + g.images.length, 0);
  const inProgressImageCount = inProgress.reduce((sum, g) => sum + g.images.length, 0);
  const availableImageCount = available.reduce((sum, g) => sum + g.images.length, 0);

  const tabs = [
    { key: 'published', emoji: '📰', label: 'Published', count: publishedImageCount },
    { key: 'in_progress', emoji: '⚡', label: 'In Progress', count: inProgressImageCount },
    { key: 'available', emoji: '📦', label: 'Custom Images', count: availableImageCount },
  ];

  if (isLoading) {
    return (
      <div className="p-8 max-w-5xl mx-auto">
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-text-primary">Image Vault</h1>
          <p className="text-text-secondary text-sm mt-1">All images across your content pipeline.</p>
        </div>
        <div className="flex border-b border-border mb-6">
          {tabs.map((tab) => (
            <div key={tab.key} className="px-4 py-2.5 text-sm text-text-muted">{tab.emoji} {tab.label}</div>
          ))}
        </div>
        <div className="space-y-4">
          {[1, 2, 3].map(i => (
            <div key={i} className="glass-card p-6">
              <div className="skeleton h-5 w-40 mb-4 rounded" />
              <div className="flex gap-4">
                {[1, 2, 3, 4].map(j => (
                  <div key={j} className="skeleton w-24 h-24 rounded-lg" />
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  const totalImages = publishedImageCount + inProgressImageCount + availableImageCount;

  return (
    <div className="p-8 max-w-5xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-text-primary">Image Vault</h1>
        <p className="text-text-secondary text-sm mt-1">
          {totalImages === 0
            ? 'No images yet. Images will appear here as you create content.'
            : `${totalImages} image${totalImages !== 1 ? 's' : ''} across your content pipeline.`}
        </p>
      </div>

      <TabBar tabs={tabs} activeTab={activeTab} onChange={setActiveTab} />

      {/* Published */}
      {activeTab === 'published' && (
        published.length === 0 ? (
          <EmptyState message="No published articles with images yet." />
        ) : (
          <div className="space-y-4">
            {published.map((group) => (
              <div key={group.article_id} className="glass-card p-4">
                <div className="flex items-center gap-2 mb-3">
                  <h3 className="text-sm font-medium text-text-primary truncate">{group.article_title}</h3>
                  <span className="text-xs text-text-muted flex-shrink-0">{timeAgo(group.published_at)}</span>
                </div>
                <div className="flex gap-4 flex-wrap">
                  {group.images.map((img) => (
                    <ImageCard key={img.id} image={img} onImageClick={openLightbox} />
                  ))}
                </div>
              </div>
            ))}
          </div>
        )
      )}

      {/* In Progress */}
      {activeTab === 'in_progress' && (
        inProgress.length === 0 ? (
          <EmptyState message="No articles in progress with images." />
        ) : (
          <div className="space-y-4">
            {inProgress.map((group) => (
              <div key={group.article_id} className="glass-card p-4">
                <div className="flex items-center gap-2 mb-3">
                  <h3 className="text-sm font-medium text-text-primary truncate">{group.article_title}</h3>
                  <StateBadge state={group.article_state} label={group.article_state_label} />
                </div>
                <div className="flex gap-4 flex-wrap">
                  {group.images.map((img) => (
                    <ImageCard key={img.id} image={img} onImageClick={openLightbox} />
                  ))}
                </div>
              </div>
            ))}
          </div>
        )
      )}

      {/* Available */}
      {activeTab === 'available' && (
        available.length === 0 ? (
          <EmptyState message="No available images. Upload images when creating new content." />
        ) : (
          <div className="space-y-4">
            {available.map((group, idx) => (
              <div key={group.group_type === 'seed' ? group.seed_id : `vault-${idx}`} className="glass-card p-4">
                <div className="flex items-center gap-2 mb-3">
                  {group.group_type === 'seed' ? (
                    <>
                      <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-accent-cyan/15 text-accent-cyan">Seed</span>
                      <h3 className="text-sm font-medium text-text-primary line-clamp-2">{group.seed_content}</h3>
                      <span className="text-xs text-text-muted flex-shrink-0">{timeAgo(group.batch_created_at)}</span>
                    </>
                  ) : (
                    <>
                      <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-accent-purple/15 text-accent-purple">Vault Pool</span>
                      <span className="text-xs text-text-muted">{group.images.length} image{group.images.length !== 1 ? 's' : ''}</span>
                    </>
                  )}
                </div>
                <div className="flex gap-4 flex-wrap">
                  {group.images.map((img) => (
                    <ImageCard
                      key={img.id}
                      image={{ ...img, source_type: group.group_type }}
                      context={group.group_type === 'vault' && img.description ? img.description : undefined}
                      onImageClick={openLightbox}
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>
        )
      )}

      {/* Lightbox */}
      {lightbox && (
        <Lightbox src={lightbox.src} alt={lightbox.alt} onClose={() => setLightbox(null)} />
      )}
    </div>
  );
}

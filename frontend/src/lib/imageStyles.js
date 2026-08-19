export const DEFAULT_IMAGE_STYLE = 'photography';
export const DEFAULT_IMAGE_SUBSTYLE = 'editorial_documentary';

export const IMAGE_STYLE_OPTIONS = [
  {
    value: 'photography',
    label: 'Photography',
    substyles: [
      { value: 'editorial_documentary', label: 'Editorial documentary' },
      { value: 'warm_lifestyle', label: 'Warm lifestyle' },
      { value: 'minimal_studio', label: 'Minimal studio' },
      { value: 'dark_cinematic', label: 'Dark cinematic' },
      { value: 'nostalgic_film', label: 'Nostalgic film' },
    ],
  },
  {
    value: 'illustration',
    label: 'Illustration',
    substyles: [
      { value: 'isometric', label: 'Isometric' },
      { value: 'flat_editorial', label: 'Flat editorial' },
      { value: 'hand_drawn', label: 'Hand-drawn' },
      { value: 'geometric', label: 'Geometric' },
      { value: 'minimal_line_art', label: 'Minimal line art' },
    ],
  },
  {
    value: 'render_3d',
    label: '3D Render',
    substyles: [
      { value: 'clay_render', label: 'Clay render' },
      { value: 'glassmorphism', label: 'Glassmorphism' },
      { value: 'futuristic_objects', label: 'Futuristic objects' },
      { value: 'minimal_product_scene', label: 'Minimal product scene' },
    ],
  },
  {
    value: 'graphic_poster',
    label: 'Graphic / Poster',
    substyles: [
      { value: 'swiss_grid', label: 'Swiss grid' },
      { value: 'bold_shapes', label: 'Bold shapes' },
      { value: 'monochrome', label: 'Monochrome' },
      { value: 'duotone', label: 'Duotone' },
    ],
  },
  {
    value: 'mixed_media',
    label: 'Mixed Media',
    substyles: [
      { value: 'collage', label: 'Collage' },
      { value: 'cut_paper', label: 'Cut-paper' },
      { value: 'risograph', label: 'Risograph' },
      { value: 'blueprint', label: 'Blueprint' },
    ],
  },
];

export function getSubstyles(style) {
  return IMAGE_STYLE_OPTIONS.find((s) => s.value === style)?.substyles || IMAGE_STYLE_OPTIONS[0].substyles;
}

export function firstSubstyle(style) {
  return getSubstyles(style)[0]?.value || DEFAULT_IMAGE_SUBSTYLE;
}

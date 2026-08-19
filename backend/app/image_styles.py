"""Image style taxonomy and prompt art-direction helpers."""

DEFAULT_IMAGE_STYLE = "photography"
DEFAULT_IMAGE_SUBSTYLE = "editorial_documentary"

IMAGE_STYLE_TAXONOMY = {
    "photography": {
        "label": "Photography",
        "substyles": {
            "editorial_documentary": "Editorial documentary",
            "warm_lifestyle": "Warm lifestyle",
            "minimal_studio": "Minimal studio",
            "dark_cinematic": "Dark cinematic",
            "nostalgic_film": "Nostalgic film",
        },
    },
    "illustration": {
        "label": "Illustration",
        "substyles": {
            "isometric": "Isometric",
            "flat_editorial": "Flat editorial",
            "hand_drawn": "Hand-drawn",
            "geometric": "Geometric",
            "minimal_line_art": "Minimal line art",
        },
    },
    "render_3d": {
        "label": "3D Render",
        "substyles": {
            "clay_render": "Clay render",
            "glassmorphism": "Glassmorphism",
            "futuristic_objects": "Futuristic objects",
            "minimal_product_scene": "Minimal product scene",
        },
    },
    "graphic_poster": {
        "label": "Graphic / Poster",
        "substyles": {
            "swiss_grid": "Swiss grid",
            "bold_shapes": "Bold shapes",
            "monochrome": "Monochrome",
            "duotone": "Duotone",
        },
    },
    "mixed_media": {
        "label": "Mixed Media",
        "substyles": {
            "collage": "Collage",
            "cut_paper": "Cut-paper",
            "risograph": "Risograph",
            "blueprint": "Blueprint",
        },
    },
}

_STYLE_DIRECTIONS = {
    ("photography", "editorial_documentary"): "realistic editorial documentary photography with natural light, grounded locations, candid magazine composition, and no stock-photo staging",
    ("photography", "warm_lifestyle"): "warm lifestyle photography with approachable lived-in scenes, bright natural light, tactile everyday details, and an optimistic editorial tone",
    ("photography", "minimal_studio"): "minimal studio photography with clean backgrounds, controlled lighting, precise object placement, and a polished editorial product feel",
    ("photography", "dark_cinematic"): "dark cinematic photography with moody contrast, directional light, deep shadows, restrained color, and a serious premium tone",
    ("photography", "nostalgic_film"): "nostalgic 35mm film photography with soft grain, retro warmth, natural imperfections, and lived-in color",
    ("illustration", "isometric"): "isometric editorial illustration with structured spatial depth, clean geometric forms, system/workflow metaphors, and a controlled SaaS-quality palette",
    ("illustration", "flat_editorial"): "flat editorial illustration with clean 2D shapes, modern blog composition, crisp silhouettes, and strong negative space",
    ("illustration", "hand_drawn"): "hand-drawn editorial illustration with organic linework, subtle texture, human imperfection, and a personal sketchbook feel",
    ("illustration", "geometric"): "geometric editorial illustration with abstract shapes, controlled patterns, data/system metaphors, and rigorous composition",
    ("illustration", "minimal_line_art"): "minimal line-art illustration with sparse elegant strokes, restrained color, generous whitespace, and quiet editorial confidence",
    ("render_3d", "clay_render"): "soft matte clay-style 3D render with rounded forms, tactile surfaces, gentle shadows, and friendly product-led composition",
    ("render_3d", "glassmorphism"): "glassmorphism 3D render with translucent layered objects, soft refractions, modern tech materials, and clean depth",
    ("render_3d", "futuristic_objects"): "futuristic 3D object render with advanced materials, precise lighting, restrained high-tech atmosphere, and no neon hologram clichés",
    ("render_3d", "minimal_product_scene"): "minimal 3D product-scene render with a clean editorial setup, carefully placed objects, soft shadows, and premium restraint",
    ("graphic_poster", "swiss_grid"): "Swiss-grid poster-style graphic composition with strict layout, modular blocks, editorial grid rhythm, and disciplined spacing, without readable text or typography",
    ("graphic_poster", "bold_shapes"): "bold-shape graphic poster art with high-contrast forms, strong editorial composition, simple silhouettes, and confident color blocking",
    ("graphic_poster", "monochrome"): "monochrome graphic poster art with one-color/limited-tone discipline, strong contrast, and consistent brand-like restraint",
    ("graphic_poster", "duotone"): "duotone graphic poster art with two-color editorial palette, simplified forms, and cohesive publication-style energy",
    ("mixed_media", "collage"): "mixed-media collage with layered paper, photo fragments, shapes, tactile depth, and an editorial magazine feel",
    ("mixed_media", "cut_paper"): "cut-paper mixed-media illustration with tactile paper layers, soft shadows, crafted shapes, and handmade dimensionality",
    ("mixed_media", "risograph"): "risograph-inspired mixed media with grainy print texture, limited colors, slight registration imperfections, and indie editorial character",
    ("mixed_media", "blueprint"): "blueprint-style mixed media with schematic grids, technical diagram energy, precise lines, and no readable labels or text",
}


def validate_image_style_pair(style: str | None, substyle: str | None) -> tuple[str, str]:
    """Return a valid canonical style pair or raise ValueError."""
    style = style or DEFAULT_IMAGE_STYLE
    substyle = substyle or DEFAULT_IMAGE_SUBSTYLE
    if style not in IMAGE_STYLE_TAXONOMY:
        raise ValueError("Invalid image_style")
    if substyle not in IMAGE_STYLE_TAXONOMY[style]["substyles"]:
        raise ValueError("Invalid image_substyle for image_style")
    return style, substyle


def style_labels(style: str, substyle: str) -> tuple[str, str]:
    style, substyle = validate_image_style_pair(style, substyle)
    meta = IMAGE_STYLE_TAXONOMY[style]
    return meta["label"], meta["substyles"][substyle]


def image_style_art_direction(style: str | None, substyle: str | None) -> str:
    """Build the hard visual-direction block injected into image prompt generation."""
    style, substyle = validate_image_style_pair(style, substyle)
    style_label, substyle_label = style_labels(style, substyle)
    direction = _STYLE_DIRECTIONS[(style, substyle)]
    return (
        f"IMAGE STYLE HARD CONSTRAINT: Use {style_label} → {substyle_label}. "
        f"Every generated image for this article must follow this visual medium and sub-style: {direction}. "
        "Do not switch to another primary medium or sub-style. No text, logos, typography, or watermarks."
    )

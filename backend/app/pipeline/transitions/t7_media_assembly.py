"""T7: MEDIA_ASSEMBLY. Image Prompter → generate images → alt texts → replace anchors."""

import json
import logging
import os
import re

import aiosqlite

from app.llm.base import LLMProvider
from app.storage.base import StorageProvider
from app.utils.ulid import generate_id
from app.utils.time import utc_now
from app.image_styles import (
    DEFAULT_IMAGE_STYLE,
    DEFAULT_IMAGE_SUBSTYLE,
    image_style_art_direction,
    validate_image_style_pair,
)

logger = logging.getLogger(__name__)


_ANCHOR_RE = re.compile(r"\[IMAGE_ANCHOR:(COVER|\d+)\]")
_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9'-]{2,}")
_STOPWORDS = {
    "about", "after", "again", "against", "also", "among", "article", "because",
    "before", "being", "between", "blog", "build", "could", "every", "first",
    "from", "have", "into", "more", "most", "other", "section", "should",
    "that", "their", "there", "these", "they", "this", "through", "title",
    "when", "where", "which", "while", "with", "without", "would", "your",
}
_GENERIC_VISUAL_TERMS = (
    "person at laptop", "woman at laptop", "man at laptop", "people at laptop",
    "women at laptop", "team at laptop", "people sitting at laptop",
    "people sitting around laptop", "people around a laptop",
    "sitting at laptop", "working at laptop", "person working", "woman working",
    "man working", "women working", "team working", "laptop", "computer",
    "desk", "office worker", "digital work", "productivity",
    "business environment", "generic office",
)
_WORKPLACE_OBJECT_TERMS = (
    "laptop", "laptops", "computer", "computers", "monitor", "monitors",
    "keyboard", "keyboards", "desk", "desks", "office", "offices",
    "workstation", "workstations", "coworking",
)
_HUMAN_WORKPLACE_TERMS = (
    "person", "people", "woman", "women", "man", "men", "team", "teams",
    "employee", "employees", "worker", "workers", "colleague", "colleagues",
    "founder", "founders", "staff", "professional", "professionals",
)
_EXPLICIT_WORKPLACE_PHOTO_TERMS = (
    "person working at a laptop",
    "people working at laptops",
    "woman at a laptop",
    "man at a laptop",
    "team around a laptop",
    "office scene",
    "workplace photography",
    "workplace portrait",
    "desk setup",
    "workstation setup",
    "remote worker at a laptop",
)
_LIFESTYLE_STILL_LIFE_TERMS = (
    "coffee", "mug", "cup", "notebook", "notepad", "journal", "tablet",
    "phone", "smartphone", "pen", "pencil", "window", "windowsill",
    "wooden table", "tabletop", "table", "earbuds", "plant", "sticky notes",
)
_COMPOSITION_FAMILIES = {
    "tabletop still life": (
        "tabletop", "table top", "on a table", "wooden table", "still life",
        "still-life", "flat lay", "flatlay", "overhead", "desk setup",
    ),
    "device close-up": (
        "phone", "smartphone", "tablet", "screen close-up", "device close-up",
    ),
    "window lifestyle": (
        "window", "windowsill", "sunlit table", "morning light",
    ),
    "paper note arrangement": (
        "notebook", "notepad", "sticky notes", "handwritten notes", "pen",
    ),
}
_SURFACE_COMPOSITION_FAMILIES = {
    "tabletop still life",
    "device close-up",
    "paper note arrangement",
}
_SURFACE_PROP_TERMS = (
    "sticky note", "sticky notes", "post-it", "post its", "paper", "papers",
    "paperwork", "document", "documents", "printout", "printouts", "worksheet",
    "schedule", "schedules", "calendar", "planner", "tablet", "ipad", "phone",
    "smartphone", "notebook", "notepad", "journal", "pen", "pencil", "cards",
    "index cards", "slips", "forms",
)

_CONCEPT_SEEDS = (
    {
        "matches": ("email", "inbox", "follow-up", "follow up", "reply", "gmail", "outlook"),
        "concept": (
            "email drafting and follow-up workflow shown as a mailroom-style sorting system "
            "with unlabeled envelopes moving through separate color-coded lanes for reply, "
            "invoice, lead-response, and scheduling tasks"
        ),
        "required_terms": (
            "email", "inbox", "reply", "follow-up", "invoice", "lead", "scheduling",
            "envelope", "sorting", "lane",
        ),
    },
    {
        "matches": ("meeting", "summary", "summaries", "action item", "transcript", "otter", "fireflies", "fathom"),
        "concept": (
            "meeting capture workflow shown inside an empty conference room after everyone "
            "has left, with a recording indicator, abstract audio waveform display, color-coded "
            "owner markers, and a calendar handoff signal shown without readable UI text"
        ),
        "required_terms": (
            "meeting", "transcript", "summary", "action item", "owner", "calendar",
            "recording", "audio", "waveform", "conference",
        ),
    },
    {
        "matches": ("feedback", "review", "reviews", "survey", "complaint", "theme"),
        "concept": (
            "customer feedback analysis workflow shown as a service counter or support room "
            "installation where unlabeled customer-response tokens flow into distinct color-coded "
            "theme clusters and priority lanes"
        ),
        "required_terms": (
            "feedback", "review", "survey", "customer", "theme", "complaint",
            "priority", "cluster", "token", "lane",
        ),
    },
    {
        "matches": ("ticket", "routing", "handoff", "support workflow", "customer support"),
        "concept": (
            "support ticket routing workflow with intake cards, routing rules, stuck-point "
            "markers, and handoff lanes"
        ),
        "required_terms": (
            "ticket", "intake", "routing", "handoff", "support", "stuck",
        ),
    },
    {
        "matches": ("intent", "taxonomy", "confidence", "escalation"),
        "concept": (
            "intent and escalation map with taxonomy cards, confidence threshold markers, "
            "handoff lane, and escalation path"
        ),
        "required_terms": (
            "intent", "taxonomy", "confidence", "threshold", "escalation", "handoff",
        ),
    },
    {
        "matches": ("knowledge base", "help center", "source material", "articles"),
        "concept": (
            "knowledge base maintenance workflow with help-center article stack, source "
            "freshness tags, and update queue"
        ),
        "required_terms": (
            "knowledge", "help center", "article", "source", "freshness", "update",
        ),
    },
)


def _keyword_overlap(text1: str, text2: str) -> int:
    """Count matching words between two texts (case-insensitive)."""
    words1 = set(text1.lower().split())
    words2 = set(text2.lower().split())
    return len(words1 & words2)


def _important_terms(*texts: object) -> set[str]:
    terms: set[str] = set()
    for text in texts:
        if isinstance(text, list):
            text = " ".join(str(item) for item in text)
        if not text:
            continue
        for word in _WORD_RE.findall(str(text).lower()):
            if word not in _STOPWORDS and not word.isdigit():
                terms.add(word)
    return terms


def _normalize_value(value: object) -> str:
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def _phrase_present(text: str, phrase: str) -> bool:
    phrase = phrase.lower().strip()
    if not phrase:
        return False
    return _contains_unnegated_term(text, phrase)


def _concept_seed_for_slot(slot_type: str, source_text: str, article_title: str, focus_keyword: str) -> dict:
    text = source_text.lower()
    scored_matches = [
        (sum(1 for term in seed["matches"] if term in text), index, seed)
        for index, seed in enumerate(_CONCEPT_SEEDS)
    ]
    matches = [
        seed for score, _index, seed in sorted(scored_matches, key=lambda item: (-item[0], item[1]))
        if score > 0
    ]

    if slot_type == "cover" and len(matches) >= 2:
        concepts = [seed["concept"] for seed in matches[:3]]
        required_terms = []
        for seed in matches[:3]:
            required_terms.extend(seed["required_terms"][:4])
        label = (
            "weekly AI time-savings toolkit"
            if all(
                any(term in text for term in required)
                for required in (("email", "inbox"), ("meeting", "summary"), ("feedback", "review", "survey"))
            )
            else "article workflow toolkit"
        )
        return {
            "visual_concept": (
                f"{label} combining "
                + "; ".join(concepts)
            ),
            "required_visual_terms": list(dict.fromkeys(required_terms)),
            "specific_visual_concept": True,
        }

    if matches:
        seed = matches[0]
        return {
            "visual_concept": seed["concept"],
            "required_visual_terms": list(seed["required_terms"]),
            "specific_visual_concept": True,
        }

    fallback_terms = list(_important_terms(article_title, focus_keyword, source_text))[:10]
    concept_terms = ", ".join(fallback_terms[:5]) or focus_keyword or article_title
    return {
        "visual_concept": f"article-specific workflow artifact built around {concept_terms}",
        "required_visual_terms": fallback_terms,
        "specific_visual_concept": False,
    }


def _outline_sections(outline: dict) -> list[dict]:
    sections = outline.get("article_outline", outline.get("sections", []))
    return sections if isinstance(sections, list) else []


def _section_heading(section: dict, fallback: str) -> str:
    return (
        section.get("subheading")
        or section.get("title")
        or section.get("heading")
        or fallback
    )


def _normalize_heading_text(text: str) -> str:
    text = re.sub(r"^#+\s*", "", text).strip().lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _section_for_anchor_position(draft: str, anchor_start: int, sections: list[dict]) -> dict | None:
    headings_by_text = {
        _normalize_heading_text(_section_heading(section, "")): section
        for section in sections
        if _section_heading(section, "")
    }
    if not headings_by_text:
        return None

    last_heading = None
    for match in re.finditer(r"(?m)^\s{0,3}#{2,6}\s+(.+?)\s*$", draft[:anchor_start]):
        last_heading = match.group(1)
    if not last_heading:
        return None

    return headings_by_text.get(_normalize_heading_text(last_heading))


def build_image_slots(
    draft: str,
    outline: dict,
    content_brief: dict | None,
    article_title: str,
    focus_keyword: str,
) -> list[dict]:
    """Build deterministic per-anchor context before the Image Prompter runs.

    COVER is article-level. Numbered anchors use the outline section where they
    physically appear, with numeric image-needed section mapping as a fallback
    for older drafts without recognizable headings.
    """
    anchors = list(_ANCHOR_RE.finditer(draft))
    sections = _outline_sections(outline)
    image_sections = [s for s in sections if s.get("image_needed")]
    content_brief = content_brief or {}
    thesis = outline.get("thesis") or outline.get("angle") or ""
    brief_description = content_brief.get("user_description") or content_brief.get("description") or ""
    brief_keywords = content_brief.get("keywords") or content_brief.get("user_keywords") or ""

    slots = []
    for position, anchor_match in enumerate(anchors):
        anchor_id = anchor_match.group(1)
        anchor = f"IMAGE_ANCHOR:{anchor_id}"
        nearby_start = max(0, anchor_match.start() - 900)
        nearby_end = min(len(draft), anchor_match.end() + 900)
        nearby_text = re.sub(
            r"\s+",
            " ",
            draft[nearby_start:nearby_end].replace(anchor_match.group(0), " "),
        ).strip()
        if anchor_id == "COVER":
            source_text = " ".join(str(point) for point in (article_title, thesis, focus_keyword))
            source_text = f"{source_text} {nearby_text[:900]}"
            concept_seed = _concept_seed_for_slot("cover", source_text, article_title, focus_keyword)
            semantic_target = (
                f"Article-level concept: {article_title}. Thesis: {thesis}. "
                f"Content brief: {brief_description}. Focus keyword: {focus_keyword}. "
                f"Visual concept seed: {concept_seed['visual_concept']}. "
                f"Required visible artifacts/process terms: {', '.join(concept_seed['required_visual_terms'])}."
            )
            slots.append({
                "anchor": anchor,
                "anchor_id": anchor_id,
                "slot_type": "cover",
                "heading": "Article cover",
                "purpose": "Represent the central point of the full article, not a single section.",
                "key_points": [point for point in (thesis, brief_description, focus_keyword) if point],
                "nearby_text": "",
                "semantic_target": re.sub(r"\s+", " ", semantic_target).strip(),
                "position": position,
                **concept_seed,
            })
            continue

        section_idx = int(anchor_id) - 1
        section = (
            _section_for_anchor_position(draft, anchor_match.start(), sections)
            or (image_sections[section_idx] if 0 <= section_idx < len(image_sections) else {})
        )
        heading = _section_heading(section, f"Section {anchor_id}")
        key_points = section.get("key_points") or []
        if isinstance(key_points, str):
            key_points = [key_points]
        purpose = section.get("purpose") or section.get("summary") or ""
        source_text = " ".join([
            heading,
            purpose,
            " ".join(str(p) for p in key_points),
            focus_keyword,
        ])
        concept_seed = _concept_seed_for_slot("inline", source_text, article_title, focus_keyword)
        semantic_target = (
            f"Inline section concept for '{heading}'. Purpose: {purpose}. "
            f"Key points: {', '.join(str(p) for p in key_points)}. "
            f"Nearby text: {nearby_text[:700]}. "
            f"Visual concept seed: {concept_seed['visual_concept']}. "
            f"Required visible artifacts/process terms: {', '.join(concept_seed['required_visual_terms'])}."
        )
        slots.append({
            "anchor": anchor,
            "anchor_id": anchor_id,
            "slot_type": "inline",
            "heading": heading,
            "purpose": purpose,
            "key_points": key_points,
            "nearby_text": nearby_text,
            "semantic_target": re.sub(r"\s+", " ", semantic_target).strip(),
            "position": position,
            "outline_section_number": section.get("section_number"),
            "brief_keywords": brief_keywords,
            **concept_seed,
        })
    return slots


def _generic_visual_count(*texts: object) -> int:
    joined = " ".join(_normalize_value(text).lower() for text in texts)
    count = 0
    for term in _GENERIC_VISUAL_TERMS:
        if _contains_unnegated_term(joined, term):
            count += 1
    return count


def _contains_unnegated_term(text: str, term: str) -> bool:
    escaped = re.escape(term)
    if re.fullmatch(r"[a-zA-Z0-9'-]+", term):
        escaped = f"{escaped}s?"
    for match in re.finditer(rf"\b{escaped}\b", text):
        prefix = text[max(0, match.start() - 160):match.start()]
        if (
            re.search(r"\b(no|not|without|avoid|exclude|excluding|never)\s+(?:[\w,-]+\s+){0,8}$", prefix)
            or "do not use" in prefix
            or "rather than" in prefix
            or "instead of" in prefix
        ):
            continue
        return True
    return False


def _slot_explicitly_allows_workplace_photo(slot: dict) -> bool:
    slot_text = " ".join(
        _normalize_value(slot.get(key)).lower()
        for key in ("heading", "purpose", "key_points", "semantic_target", "nearby_text")
    )
    return any(phrase in slot_text for phrase in _EXPLICIT_WORKPLACE_PHOTO_TERMS)


def _generic_workplace_violations(slot: dict, *texts: object) -> list[str]:
    """Reject generic people/laptop/office prompts before image generation."""
    joined = " ".join(_normalize_value(text).lower() for text in texts)
    if not joined or _slot_explicitly_allows_workplace_photo(slot):
        return []

    object_hits = [term for term in _WORKPLACE_OBJECT_TERMS if _contains_unnegated_term(joined, term)]
    human_hits = [term for term in _HUMAN_WORKPLACE_TERMS if _contains_unnegated_term(joined, term)]
    phrase_hits = [
        term for term in _GENERIC_VISUAL_TERMS
        if " " in term and _contains_unnegated_term(joined, term)
    ]

    violations = []
    if phrase_hits:
        violations.append(f"generic people/laptop scene: {', '.join(sorted(set(phrase_hits)))}")
    if object_hits:
        violations.append(f"generic workplace object scene: {', '.join(sorted(set(object_hits)))}")
    if object_hits and human_hits:
        violations.append(
            "generic human workplace scene: "
            f"{', '.join(sorted(set(human_hits[:4])))} with {', '.join(sorted(set(object_hits[:4])))}"
        )
    return violations


def _prompt_has_style_constraint(prompt: str, image_style_direction: str | None) -> bool:
    if not image_style_direction:
        return True
    prompt_lower = prompt.lower()
    first_line = image_style_direction.splitlines()[0].strip()
    if first_line[:80].lower() in prompt_lower:
        return True
    if "image style hard constraint" not in prompt_lower:
        return False
    # The exact style sentence can be paraphrased by the model. Require the
    # hard-constraint marker plus the concrete medium/sub-style nouns instead.
    style_words = [
        word for word in _WORD_RE.findall(first_line.lower())
        if word not in _STOPWORDS and word not in {"hard", "constraint", "generated", "image"}
    ]
    return len(set(style_words) & set(_WORD_RE.findall(prompt_lower))) >= min(3, len(style_words))


def _required_visual_term_hits(slot: dict, *texts: object) -> set[str]:
    joined = " ".join(_normalize_value(text).lower() for text in texts)
    return {
        term for term in slot.get("required_visual_terms", [])
        if _phrase_present(joined, str(term))
    }


def _lifestyle_still_life_terms(*texts: object) -> set[str]:
    joined = " ".join(_normalize_value(text).lower() for text in texts)
    return {
        term for term in _LIFESTYLE_STILL_LIFE_TERMS
        if _phrase_present(joined, term)
    }


def _composition_family(*texts: object) -> str | None:
    joined = " ".join(_normalize_value(text).lower() for text in texts)
    for family, terms in _COMPOSITION_FAMILIES.items():
        if any(_phrase_present(joined, term) for term in terms):
            return family
    return None


def _surface_prop_hits(*texts: object) -> set[str]:
    joined = " ".join(_normalize_value(text).lower() for text in texts)
    return {
        term for term in _SURFACE_PROP_TERMS
        if _phrase_present(joined, term)
    }


def _workflow_relevance_violations(
    slot: dict,
    required_hits: set[str],
    subject: str,
    composition: str,
    concrete_objects: str,
    why: str,
) -> list[str]:
    if not slot.get("specific_visual_concept"):
        return []

    reasons: list[str] = []
    family = _composition_family(subject, composition, concrete_objects)
    surface_hits = _surface_prop_hits(subject, composition, concrete_objects, why)
    specific_hit_floor = 2 if slot.get("slot_type") == "inline" else 1
    if required_hits and len(required_hits) < min(specific_hit_floor, len(slot.get("required_visual_terms", []))):
        reasons.append("missing slot-specific workflow artifacts")

    if family in _SURFACE_COMPOSITION_FAMILIES and len(required_hits) < 2:
        reasons.append("generic surface composition without enough workflow substance")

    generic_subject_hits = _surface_prop_hits(subject, concrete_objects)
    if generic_subject_hits and len(required_hits) < 2 and len(generic_subject_hits) >= 2:
        reasons.append(
            "primary subject leans on generic productivity props instead of the required workflow"
        )

    surface_only_subject = generic_subject_hits and not (
        _important_terms(subject, concrete_objects) - _important_terms(*generic_subject_hits)
    )
    if family in _SURFACE_COMPOSITION_FAMILIES and surface_only_subject and len(required_hits) < 3:
        reasons.append("surface still-life subject is too generic for this workflow slot")

    if slot.get("slot_type") == "cover":
        cover_terms = {
            "email", "reply", "follow-up", "meeting", "summary", "action item",
            "feedback", "review", "survey", "priority", "theme",
        }
        cover_hits = {term for term in required_hits if term in cover_terms}
        if "weekly ai time-savings toolkit" in slot.get("visual_concept", "").lower() and len(cover_hits) < 3:
            reasons.append("cover does not unify the article's email, meeting, and feedback workflows")

    return reasons


def _scene_family_guidance(slot: dict | None) -> str:
    slot = slot or {}
    concept = _normalize_value(slot.get("visual_concept")).lower()
    required = {str(term).lower() for term in slot.get("required_visual_terms", [])}
    if slot.get("slot_type") == "cover" and "weekly ai time-savings toolkit" in concept:
        return (
            "Show one unified weekly AI time-saving system with three connected zones: inbox triage, "
            "meeting capture-to-action handoff, and feedback clustering/prioritization. Keep all "
            "markers blank or abstract, not labeled. Use one room-scale scene, not a split-panel collage "
            "or tabletop montage."
        )
    if {"email", "inbox", "reply"} & required:
        return (
            "Prefer a mail-sorting lane, inbox triage station, or dispatch rack with envelopes, reply bins, "
            "invoice reminders, and scheduling lanes. Distinguish lanes with color or shape only, not readable labels. "
            "A freestanding rack, chute, or wall-mounted sorter should dominate the composition, not a desk surface."
        )
    if {"meeting", "transcript", "summary", "action item"} & required:
        return (
            "Prefer an empty meeting room, post-call capture station, or recording-to-action handoff scene with "
            "a recorder light, waveform display, owner markers, and calendar handoff. Use blank cards, abstract icons, "
            "or color-coded markers instead of readable writing or UI text. Keep it room-scale or wall-mounted; "
            "do not make a tablet, paper pad, or tabletop the main subject."
        )
    if {"feedback", "review", "survey", "theme", "priority"} & required:
        return (
            "Prefer a service wall, support room, or feedback clustering installation where customer-response "
            "tokens move into theme groups and priority lanes. Show clusters through color and grouping, not labels. "
            "Use vertical bins, pegboards, or mounted clear channels instead of piles of papers on a desk."
        )
    if {"ticket", "routing", "handoff"} & required:
        return "Prefer an intake board, routing lane, or operations panel rather than generic desk props."
    return "Prefer a real environment, installation, or process scene with depth instead of a flat surface arrangement."


def _validate_image_prompter_output(
    result: dict | None,
    slots: list[dict],
    image_style_direction: str | None = None,
) -> tuple[bool, list[str], list[dict]]:
    """Validate relevance/diversity before Gemini image generation spends money."""
    if not isinstance(result, dict):
        return False, ["Image Prompter did not return a JSON object"], []

    images = result.get("images")
    if not isinstance(images, list):
        return False, ["Image Prompter output missing images list"], []

    expected_anchors = [slot["anchor"] for slot in slots]
    by_anchor = {img.get("anchor"): img for img in images if isinstance(img, dict)}
    reasons: list[str] = []

    missing = [anchor for anchor in expected_anchors if anchor not in by_anchor]
    extra = [anchor for anchor in by_anchor if anchor not in expected_anchors]
    if missing:
        reasons.append(f"missing anchors: {', '.join(missing)}")
    if extra:
        reasons.append(f"unexpected anchors: {', '.join(extra)}")

    ordered_images = [by_anchor[anchor] for anchor in expected_anchors if anchor in by_anchor]
    subjects: dict[str, str] = {}
    compositions: dict[str, str] = {}
    generic_prompt_count = 0
    lifestyle_by_anchor: dict[str, set[str]] = {}
    composition_family_by_anchor: dict[str, str] = {}

    for slot, img in zip(slots, ordered_images):
        anchor = slot["anchor"]
        prompt = _normalize_value(img.get("prompt"))
        subject = _normalize_value(img.get("primary_subject")).lower()
        composition = _normalize_value(img.get("composition_type")).lower()
        concrete_objects = _normalize_value(img.get("concrete_objects"))
        why = _normalize_value(img.get("why_this_matches"))
        semantic_target = _normalize_value(img.get("semantic_target"))

        if not prompt:
            reasons.append(f"{anchor}: missing prompt")
        if not subject:
            reasons.append(f"{anchor}: missing primary_subject")
        if not composition:
            reasons.append(f"{anchor}: missing composition_type")
        if not concrete_objects:
            reasons.append(f"{anchor}: missing concrete_objects")
        if not why:
            reasons.append(f"{anchor}: missing why_this_matches")

        if subject:
            previous = subjects.get(subject)
            if previous:
                reasons.append(f"repeated primary_subject '{subject}' in {previous} and {anchor}")
            subjects[subject] = anchor
        if composition:
            previous = compositions.get(composition)
            if previous:
                reasons.append(f"repeated composition_type '{composition}' in {previous} and {anchor}")
            compositions[composition] = anchor

        if _generic_visual_count(subject, composition, prompt):
            generic_prompt_count += 1
        for violation in _generic_workplace_violations(
            slot, subject, composition, concrete_objects, why, prompt
        ):
            reasons.append(f"{anchor}: {violation}")
        required_hits = _required_visual_term_hits(
            slot, subject, concrete_objects, composition, why
        )
        for violation in _workflow_relevance_violations(
            slot, required_hits, subject, composition, concrete_objects, why
        ):
            reasons.append(f"{anchor}: {violation}")

        if not _prompt_has_style_constraint(prompt, image_style_direction):
            reasons.append(f"{anchor}: selected style constraint missing from prompt")
        if "no text" not in prompt.lower() or "no watermark" not in prompt.lower():
            reasons.append(f"{anchor}: prompt missing no-text/no-watermark constraint")

        lifestyle_terms = _lifestyle_still_life_terms(subject, concrete_objects, composition, why, prompt)
        if lifestyle_terms:
            lifestyle_by_anchor[anchor] = lifestyle_terms
        family = _composition_family(subject, composition, concrete_objects, prompt)
        if family:
            composition_family_by_anchor[anchor] = family

        slot_terms = _important_terms(
            slot.get("heading"),
            slot.get("purpose"),
            slot.get("key_points"),
            slot.get("semantic_target"),
        )
        prompt_terms = _important_terms(prompt, subject, concrete_objects, why, semantic_target)
        overlap = slot_terms & prompt_terms
        if slot["slot_type"] == "inline" and len(overlap) < 2:
            reasons.append(f"{anchor}: prompt lacks concrete section concepts")
        if slot["slot_type"] == "cover":
            article_terms = _important_terms(slot.get("semantic_target"))
            if len(article_terms & prompt_terms) < 2:
                reasons.append(f"{anchor}: cover prompt lacks article-level concepts")

    if generic_prompt_count > 1:
        reasons.append("too many generic laptop/person/desk prompts")

    if len(ordered_images) >= 3:
        lifestyle_heavy = [
            anchor for anchor, terms in lifestyle_by_anchor.items()
            if len(terms) >= 2
        ]
        all_lifestyle_terms: dict[str, list[str]] = {}
        for anchor, terms in lifestyle_by_anchor.items():
            for term in terms:
                all_lifestyle_terms.setdefault(term, []).append(anchor)
        repeated_lifestyle_terms = {
            term: anchors for term, anchors in all_lifestyle_terms.items()
            if len(anchors) >= 2
        }
        family_counts: dict[str, list[str]] = {}
        for anchor, family in composition_family_by_anchor.items():
            family_counts.setdefault(family, []).append(anchor)
        repeated_families = {
            family: anchors for family, anchors in family_counts.items()
            if len(anchors) >= 2
        }
        if len(lifestyle_heavy) >= 3 or (repeated_lifestyle_terms and repeated_families):
            reasons.append(
                "samey lifestyle still-life repetition across image slots: "
                f"props={', '.join(sorted(repeated_lifestyle_terms)) or 'lifestyle tabletop props'}, "
                f"compositions={', '.join(sorted(repeated_families)) or 'still-life family'}"
            )
        surface_family_anchors = [
            anchor for anchor, family in composition_family_by_anchor.items()
            if family in _SURFACE_COMPOSITION_FAMILIES
        ]
        if len(surface_family_anchors) >= 3:
            reasons.append(
                "too many slots collapse into generic flat-lay/tabletop/device-paper compositions: "
                + ", ".join(surface_family_anchors)
            )

    return len(reasons) == 0, reasons, ordered_images


async def _find_vault_match(
    db: aiosqlite.Connection,
    user_id: str,
    section_heading: str,
    prompt_text: str,
) -> dict | None:
    """Find a vault image matching by keyword overlap."""
    cursor = await db.execute(
        "SELECT * FROM vault_images WHERE user_id = ? AND used_count < 3",
        (user_id,),
    )
    candidates = [dict(r) for r in await cursor.fetchall()]

    match_text = f"{section_heading} {prompt_text}"
    best_match = None
    best_score = 0

    for img in candidates:
        desc = img.get("description", "") or ""
        tags_str = " ".join(json.loads(img.get("tags", "[]") or "[]"))
        img_text = f"{desc} {tags_str}"
        score = _keyword_overlap(match_text, img_text)
        if score > best_score:
            best_score = score
            best_match = img

    return best_match if best_score > 0 else None


# Trello #331: fallback prompt matches Image Prompter v4 route-based art direction.
# Default is photography; illustration is chosen only when user revision notes explicitly
# signal it (illustrat/drawn/painted/vector, case-insensitive).
_FALLBACK_ILLUSTRATION_TRIGGERS = ("illustrat", "drawn", "painted", "vector")

_FALLBACK_PHOTOGRAPHY_TEMPLATE = (
    "Editorial photograph for a blog article titled '{article_title}' about {focus_keyword}, "
    "specifically the section '{section_heading}'. Shoot a real scene or object that carries "
    "the section's idea — either the literal subject if it's physical, a concrete process "
    "artifact, a document/workflow object, equipment, or a real evocative environment that "
    "connects to the subject if it's abstract. Do not use generic people, laptop, computer, "
    "desk, office, or team-at-work imagery unless the section explicitly asks for that exact "
    "workplace scene. Directional natural light, intentional color, "
    "strong focal point, atmosphere over information. Magazine feature quality, not stock. "
    "No handshakes, no lightbulbs, no people sitting around laptops, no people pointing at "
    "whiteboards, no coffee cups beside notebooks, no meaningful sunsets. No text, no logos, "
    "no typography. No recognizable "
    "faces of real people. 16:9 aspect ratio, high resolution, no watermarks."
)

_FALLBACK_ILLUSTRATION_TEMPLATE = (
    "Editorial illustration for a blog article titled '{article_title}' about {focus_keyword}, "
    "specifically the section '{section_heading}'. Warm hand-made feel with visible texture "
    "and a considered palette of 3-4 colors. Depict a single specific scene or object that "
    "carries the section's idea concretely. Avoid: hourglasses with coins, mazes of doors, "
    "cracked vessels, candles on books, figures climbing staircases, glowing interfaces, "
    "brains made of light. One clear focal element, balanced negative space, editorial "
    "magazine quality. No text, no words, no logos, no typography. 16:9 aspect ratio, no "
    "watermarks."
)


def _build_fallback_prompt(
    article_title: str,
    focus_keyword: str,
    section_heading: str,
    user_revision_notes: str | None = None,
    image_style_direction: str | None = None,
    slot: dict | None = None,
) -> str:
    """Build a boilerplate image prompt when the Image Prompter fails.

    Defaults to a photography variant. Uses the illustration variant only when
    user revision notes explicitly signal an illustrated style. When revision
    notes are present, they are prepended as a highest-priority override line.
    """
    notes = (user_revision_notes or "").strip()

    slot = slot or {}
    slot_context = ""
    if slot:
        required_terms = ", ".join(str(term) for term in slot.get("required_visual_terms", [])[:8])
        slot_context = (
            f"Slot: {slot.get('slot_type', 'image')} for {slot.get('anchor', '')}. "
            f"Heading: {slot.get('heading', '')}. "
            f"Exact visual concept to depict: {slot.get('visual_concept', '')}. "
            f"Required visible artifacts/process terms: {required_terms}. "
            f"Purpose: {slot.get('purpose', '')} "
            f"Key points: {_normalize_value(slot.get('key_points'))} "
        )

    if image_style_direction:
        scene_guidance = _scene_family_guidance(slot)
        body = (
            f"{image_style_direction}\n\n"
            f"Create a blog article image for '{article_title}' about {focus_keyword}. "
            f"{slot_context or f'Specifically the section {section_heading}.'} "
            f"Preferred scene family: {scene_guidance} "
            "Depict that exact visual concept, not a generic lifestyle scene. Make the visible "
            "subject include at least two of the required artifacts/process terms above as "
            "physical workflow artifacts. Keep the style consistent with the article's other "
            "images, but make this slot substantively different in setting and composition. "
            "Every paper, card, envelope, sign, whiteboard, device, or calendar must stay blank, "
            "unlabeled, or abstract. Convey categories through color, spacing, arrows, stacks, "
            "or simple geometric markers, never readable words, handwriting, numbers, or UI text. "
            "When the preferred scene family implies a room, wall, rack, or installation, make that "
            "environment the dominant composition and keep any horizontal work surface incidental in the background only. "
            "Choose a scene with depth, environment, motion, or equipment; avoid collapsing into "
            "the same flat-lay, tabletop, paper-note, or device close-up composition family used "
            "by other slots unless this workflow genuinely requires it. Do not use generic people, "
            "laptop, computer, desk, office, "
            "or team-at-work imagery unless this slot explicitly asks for that exact workplace "
            "scene. Use one clear focal element, a composition distinct from the other article "
            "images, coherent palette, and editorial quality. No text, no words, no logos, no "
            "typography. "
            "16:9 aspect ratio, high resolution, no watermarks."
        )
        if notes:
            return f"User revision notes (apply inside the selected image style): {notes}\n\n{body}"
        return body

    use_illustration = bool(notes) and any(
        trigger in notes.lower() for trigger in _FALLBACK_ILLUSTRATION_TRIGGERS
    )

    template = (
        _FALLBACK_ILLUSTRATION_TEMPLATE if use_illustration else _FALLBACK_PHOTOGRAPHY_TEMPLATE
    )
    body = template.format(
        article_title=article_title,
        focus_keyword=focus_keyword,
        section_heading=section_heading,
    )

    if notes:
        return f"User direction (apply above all else): {notes}\n\n{body}"
    return body


async def _get_image_prompter_output(
    llm: LLMProvider,
    article_title: str,
    focus_keyword: str,
    draft: str,
    image_slots: list[dict],
    user_photo_descriptions: list[dict] | None = None,
    user_revision_notes: str | None = None,
    image_style_direction: str | None = None,
    diagnostics: dict | None = None,
) -> dict | None:
    """Call the Image Prompter LLM and validate/retry. Returns dict or None on failure."""
    validation_reasons: list[str] = []
    for attempt in range(2):
        try:
            notes = user_revision_notes
            if validation_reasons:
                correction = (
                    "Internal retry correction for image prompts: fix these validation failures before outputting JSON: "
                    + "; ".join(validation_reasons[:8])
                )
                notes = f"{notes}\n\n{correction}" if notes else correction

            result = await llm.generate_image_prompts(
                article_title, focus_keyword, draft,
                image_slots=image_slots,
                user_photo_descriptions=user_photo_descriptions,
                user_revision_notes=notes,
                image_style_direction=image_style_direction,
            )

            valid, reasons, ordered_images = _validate_image_prompter_output(
                result,
                image_slots,
                image_style_direction=image_style_direction,
            )
            if diagnostics is not None:
                diagnostics.setdefault("image_prompter_attempts", []).append({
                    "attempt": attempt + 1,
                    "valid": valid,
                    "reasons": reasons,
                })
            if not valid:
                validation_reasons = reasons
                logger.warning(
                    "Image Prompter attempt %s failed validation: %s",
                    attempt + 1,
                    "; ".join(reasons),
                )
                if attempt == 0:
                    continue
                return None

            result["images"] = ordered_images
            return result

        except Exception as e:
            logger.warning(f"Image Prompter attempt {attempt + 1} failed: {e}")
            if attempt == 0:
                continue

    return None


async def run_media_assembly(
    db: aiosqlite.Connection,
    config,
    article_id: str,
    llm: LLMProvider,
    storage: StorageProvider | None = None,
) -> dict:
    """Run media assembly for an article."""
    cursor = await db.execute(
        """SELECT a.*, u.id as uid, u.image_style as user_image_style, u.image_substyle as user_image_substyle
           FROM articles a JOIN users u ON a.user_id = u.id WHERE a.id = ?""",
        (article_id,),
    )
    article = dict(await cursor.fetchone())
    user_id = article["user_id"]

    # Get article title from idea
    cursor = await db.execute("SELECT title FROM ideas WHERE id = ?", (article["idea_id"],))
    idea_row = await cursor.fetchone()
    article_title = idea_row["title"] if idea_row else ""

    outline = json.loads(article["outline_json"]) if article["outline_json"] else {}
    seo_meta = json.loads(article["seo_meta"]) if article["seo_meta"] else {}
    focus_keyword = seo_meta.get("focus_keyword") or seo_meta.get("target_keyword") or seo_meta.get("keywords", "")

    content_brief = {}
    if article.get("content_brief"):
        try:
            content_brief = json.loads(article["content_brief"])
        except (json.JSONDecodeError, TypeError):
            content_brief = {}

    effective_style = content_brief.get("image_style") or article.get("user_image_style") or DEFAULT_IMAGE_STYLE
    effective_substyle = content_brief.get("image_substyle") or article.get("user_image_substyle") or DEFAULT_IMAGE_SUBSTYLE
    try:
        effective_style, effective_substyle = validate_image_style_pair(effective_style, effective_substyle)
    except ValueError:
        effective_style, effective_substyle = DEFAULT_IMAGE_STYLE, DEFAULT_IMAGE_SUBSTYLE
    image_style_direction = image_style_art_direction(effective_style, effective_substyle)

    # Get latest humanized draft
    cursor = await db.execute(
        """SELECT * FROM draft_iterations WHERE article_id = ?
           ORDER BY iteration_number DESC LIMIT 1""",
        (article_id,),
    )
    draft_iter = dict(await cursor.fetchone())
    draft = draft_iter["humanized_draft_md"] or draft_iter["raw_draft_md"]

    image_slots = build_image_slots(draft, outline, content_brief, article_title, focus_keyword)

    images_data = []
    prepared_images = []

    # Load seed images for this article's batch (all seeds in the batch)
    cursor = await db.execute(
        """SELECT si.* FROM seed_images si
           JOIN seeds s ON si.seed_id = s.id
           JOIN seed_batches sb ON s.batch_id = sb.id
           JOIN ideas i ON i.batch_id = sb.id
           WHERE i.id = ?""",
        (article["idea_id"],),
    )
    seed_images = [dict(r) for r in await cursor.fetchall()]

    # Build role-based image mapping: cover→COVER anchor, body→first body anchor
    role_to_anchor = {}
    for img in seed_images:
        role = img.get("image_role")
        if role == "cover":
            role_to_anchor.setdefault("cover", img)
        elif role == "body":
            role_to_anchor.setdefault("body", img)

    # Build user photo descriptions for the Image Prompter
    user_photo_descriptions = []
    for img in seed_images:
        if img.get("description"):
            user_photo_descriptions.append({
                "role": img.get("image_role") or "body",
                "description": img["description"],
            })

    # ─── Get user revision notes (if article was sent back for revision) ───
    cursor = await db.execute(
        """SELECT revision_notes FROM article_reviews
           WHERE article_id = ? AND status = 'revision_requested'
           ORDER BY review_number DESC LIMIT 1""",
        (article_id,),
    )
    row = await cursor.fetchone()
    user_revision_notes = row["revision_notes"] if row else None

    # End any implicit read transaction before slow external work. Media assembly
    # may spend minutes in LLM/image generation/storage; keep SQLite write locks
    # limited to the final persistence block below.
    await db.commit()

    # ─── Image Prompter: get per-anchor prompts from LLM ───
    image_diagnostics = {
        "image_style": effective_style,
        "image_substyle": effective_substyle,
        "slot_count": len(image_slots),
        "slots": [
            {
                "anchor": slot["anchor"],
                "slot_type": slot["slot_type"],
                "heading": slot["heading"],
                "semantic_target": slot["semantic_target"][:500],
            }
            for slot in image_slots
        ],
    }

    prompter_output = await _get_image_prompter_output(
        llm, article_title, focus_keyword, draft, image_slots,
        user_photo_descriptions=user_photo_descriptions if user_photo_descriptions else None,
        user_revision_notes=user_revision_notes,
        image_style_direction=image_style_direction,
        diagnostics=image_diagnostics,
    )

    # Build anchor → prompt mapping from Image Prompter output
    prompter_prompts = {}
    if prompter_output and prompter_output.get("images"):
        image_diagnostics["final_subjects"] = [
            {
                "anchor": img_entry.get("anchor"),
                "primary_subject": img_entry.get("primary_subject"),
                "composition_type": img_entry.get("composition_type"),
            }
            for img_entry in prompter_output["images"]
        ]
        for img_entry in prompter_output["images"]:
            anchor = img_entry.get("anchor")
            prompt = img_entry.get("prompt")
            if anchor and prompt:
                prompter_prompts[anchor] = prompt
    else:
        image_diagnostics["used_fallback_prompts"] = True

    for i, slot in enumerate(image_slots):
        anchor_key = slot["anchor"]
        anchor_id = slot["anchor_id"]
        heading = slot.get("heading") or f"Section {i + 1}"

        # Get the prompt: Image Prompter output or fallback
        image_prompt = prompter_prompts.get(anchor_key)
        if not image_prompt:
            image_diagnostics.setdefault("fallback_anchors", []).append(anchor_key)
            image_prompt = _build_fallback_prompt(
                article_title, focus_keyword, heading,
                user_revision_notes=user_revision_notes,
                image_style_direction=image_style_direction,
                slot=slot,
            )
            fallback_violations = _generic_workplace_violations(slot, image_prompt)
            if fallback_violations:
                raise RuntimeError(
                    f"Fallback image prompt failed relevance guard for anchor {anchor_key}: "
                    + "; ".join(fallback_violations)
                )
            logger.info(f"Using fallback prompt for anchor {anchor_key}")

        # Priority 1: Use role-mapped seed images if available
        # cover role → IMAGE_ANCHOR:COVER, body role → IMAGE_ANCHOR:1
        seed_img = None
        if anchor_id == "COVER" and "cover" in role_to_anchor:
            seed_img = role_to_anchor.pop("cover")
        elif anchor_id != "COVER" and "body" in role_to_anchor:
            seed_img = role_to_anchor.pop("body")
        elif not role_to_anchor and i < len(seed_images):
            # Fallback: no role-based mapping, use positional (legacy behavior)
            remaining = [img for img in seed_images if img["id"] not in {d.get("_used_seed_id") for d in images_data}]
            if remaining:
                seed_img = remaining[0]

        if seed_img:
            img_id = generate_id()
            storage_url = seed_img["storage_path"]
            if storage and os.path.exists(seed_img["storage_path"]):
                with open(seed_img["storage_path"], "rb") as f:
                    img_bytes = f.read()
                storage_key = f"articles/{article_id}/{img_id}.png"
                storage_url = await storage.upload(storage_key, img_bytes, content_type=seed_img["mime_type"])

            prepared_images.append({
                "id": img_id,
                "article_id": article_id,
                "anchor_index": anchor_id,
                "source_type": "seed",
                "vault_image_id": None,
                "generation_prompt": None,
                "section_heading": heading,
                "image_guidance": image_prompt,
                "storage_url": storage_url,
                "width": None,
                "height": None,
                "alt_text": None,
            })
            images_data.append({
                "id": img_id, "anchor": anchor_id, "section_heading": heading,
                "image_guidance": image_prompt, "storage_url": storage_url,
                "_used_seed_id": seed_img["id"],
            })
            continue

        # Priority 2: Try vault match.
        # DraftSpring #374: general vault images do not carry reliable style metadata, so
        # automatic keyword reuse must not bypass the effective profile/brief style.
        vault_match = None

        img_id = generate_id()
        if vault_match:
            prepared_images.append({
                "id": img_id,
                "article_id": article_id,
                "anchor_index": anchor_id,
                "source_type": "vault",
                "vault_image_id": vault_match["id"],
                "generation_prompt": None,
                "section_heading": heading,
                "image_guidance": image_prompt,
                "storage_url": vault_match["storage_url"],
                "width": None,
                "height": None,
                "alt_text": None,
            })
            images_data.append({
                "id": img_id, "anchor": anchor_id, "section_heading": heading,
                "image_guidance": image_prompt, "storage_url": vault_match["storage_url"],
            })
        else:
            # Priority 3: Generate image using Image Prompter's prompt
            img_width = None
            img_height = None
            image_bytes = await llm.generate_image(image_prompt)
            if not image_bytes:
                raise RuntimeError(f"Image generation returned no bytes for anchor {anchor_id}")

            if storage:
                storage_key = f"articles/{article_id}/{img_id}.png"
                storage_url = await storage.upload(storage_key, image_bytes, content_type="image/png")
            else:
                storage_url = f"local://images/{img_id}.png"

            if not storage_url:
                raise RuntimeError(f"Image upload returned no storage URL for anchor {anchor_id}")

            # Extract dimensions when possible, but don't treat metadata probing
            # as media replacement failure once generation/upload succeeded.
            try:
                from io import BytesIO
                from PIL import Image as PILImage
                img_obj = PILImage.open(BytesIO(image_bytes))
                img_width, img_height = img_obj.size
            except Exception:
                logger.warning("Could not determine generated image dimensions for anchor %s", anchor_id)

            prepared_images.append({
                "id": img_id,
                "article_id": article_id,
                "anchor_index": anchor_id,
                "source_type": "generated",
                "vault_image_id": None,
                "generation_prompt": image_prompt,
                "section_heading": heading,
                "image_guidance": image_prompt,
                "storage_url": storage_url,
                "width": img_width,
                "height": img_height,
                "alt_text": None,
            })
            images_data.append({
                "id": img_id, "anchor": anchor_id, "section_heading": heading,
                "image_guidance": image_prompt, "storage_url": storage_url,
            })

    missing_urls = [img["anchor_index"] for img in prepared_images if not img.get("storage_url")]
    if missing_urls:
        raise RuntimeError(f"Media replacement missing storage URLs for anchors: {', '.join(missing_urls)}")

    # Generate alt texts
    if images_data:
        try:
            alt_result = await llm.generate_alt_texts(focus_keyword, images_data)
            alt_texts = alt_result.get("alt_texts", [])
            if not alt_texts and isinstance(alt_result, dict):
                for key in ("alts", "results", "descriptions"):
                    if key in alt_result and isinstance(alt_result[key], list):
                        alt_texts = alt_result[key]
                        break
            for j, img in enumerate(images_data):
                if j < len(alt_texts) and isinstance(alt_texts[j], str):
                    img["alt_text"] = alt_texts[j]
                    for prepared in prepared_images:
                        if prepared["id"] == img["id"]:
                            prepared["alt_text"] = alt_texts[j]
                            break
        except Exception:
            raise RuntimeError("Alt text generation failed") from None

        missing_alt_texts = [
            img["anchor_index"] for img in prepared_images
            if not isinstance(img.get("alt_text"), str) or not img["alt_text"].strip()
        ]
        if missing_alt_texts:
            raise RuntimeError(f"Alt text generation returned incomplete results for anchors: {', '.join(missing_alt_texts)}")

    # Replace IMAGE_ANCHOR tags in draft with image markdown
    all_images = prepared_images

    for img in all_images:
        url = img["storage_url"] or "placeholder.png"
        alt = img["alt_text"] or ""
        anchor_tag = f"[IMAGE_ANCHOR:{img['anchor_index']}]"
        replacement = f"![{alt}]({url})"
        draft = draft.replace(anchor_tag, replacement)

    now = utc_now()

    # Persist the full media replacement in one short transaction. Existing
    # usable images remain visible if generation/upload/alt text fails before
    # this point; they are only replaced once the new set is ready to commit.
    await db.execute("DELETE FROM article_images WHERE article_id = ?", (article_id,))

    for img in prepared_images:
        await db.execute(
            """INSERT INTO article_images
               (id, article_id, anchor_index, source_type, vault_image_id, generation_prompt,
                section_heading, image_guidance, storage_url, width, height, alt_text, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                img["id"],
                img["article_id"],
                img["anchor_index"],
                img["source_type"],
                img["vault_image_id"],
                img["generation_prompt"],
                img["section_heading"],
                img["image_guidance"],
                img["storage_url"],
                img["width"],
                img["height"],
                img["alt_text"],
                now,
            ),
        )
        if img["source_type"] == "vault" and img["vault_image_id"]:
            await db.execute(
                "UPDATE vault_images SET used_count = used_count + 1 WHERE id = ?",
                (img["vault_image_id"],),
            )

    # Update draft with replaced anchors
    await db.execute(
        "UPDATE draft_iterations SET humanized_draft_md = ? WHERE id = ?",
        (draft, draft_iter["id"]),
    )

    # Log media assembly completion event
    event_id = generate_id()
    await db.execute(
        """INSERT INTO pipeline_events (id, article_id, user_id, event_type, from_state, to_state, payload, created_at)
           VALUES (?, ?, ?, 'state_transition', 'MEDIA_ASSEMBLY', 'WAITING_CHECKPOINT_2', ?, ?)""",
        (event_id, article_id, user_id,
         json.dumps({"images_count": len(all_images), "image_diagnostics": image_diagnostics}), now),
    )

    await db.commit()

    # Run T8: create review record, send CP2 magic link email
    from app.pipeline.transitions.t8_to_checkpoint_2 import run_to_checkpoint_2
    await run_to_checkpoint_2(db, config, article_id)

    return {"success": True, "images_count": len(all_images)}

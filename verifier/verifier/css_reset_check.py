# verifier/css_reset_check.py

import re
from pathlib import Path

_STAR_BLOCK_RE = re.compile(r'(?<![,\w])(\*)\s*\{([^}]*)\}', re.DOTALL)
_TAILWIND_SPACING_RE = re.compile(
    r'\b(?:p|px|py|pt|pb|pl|pr|m|mx|my|mt|mb|ml|mr|gap|space-[xy])-\d'
)
_CLASSNAME_VALUE_RE = re.compile(
    r'className\s*=\s*(?:'
    r'"([^"]*)"'
    r"|'([^']*)'"
    r'|`([^`]*)`'
    r'|\{[`\'"]([^`\'"]*)[`\'"]\}'
    r')',
    re.DOTALL
)
_SEMANTIC_TAGS = r'h[1-6]|p|ul|ol|li|section|article|nav|header|footer|aside|blockquote|pre'
_SEMANTIC_TAG_RE = re.compile(
    r'<(' + _SEMANTIC_TAGS + r')\b([^>]*?)(?:/>|>)',
    re.DOTALL
)
_EXPLICIT_SPACING_RE = re.compile(
    r'\b(?:m|mx|my|mt|mb|ml|mr|p|px|py|pt|pb|pl|pr)-\d'
)


def _css_has_star_reset(css_text: str) -> bool:
    for m in _STAR_BLOCK_RE.finditer(css_text):
        preceding = css_text[:m.start()].rstrip()
        if preceding and preceding[-1] in ',{':
            continue
        block = m.group(2)
        if re.search(r'\bmargin\s*:\s*0\b', block) and re.search(r'\bpadding\s*:\s*0\b', block):
            return True
    return False


def _uses_tailwind_spacing(src_dir: Path) -> bool:
    for tsx_file in src_dir.rglob('*.tsx'):
        content = tsx_file.read_text(encoding='utf-8', errors='ignore')
        for m in _CLASSNAME_VALUE_RE.finditer(content):
            value = m.group(1) or m.group(2) or m.group(3) or m.group(4) or ''
            if _TAILWIND_SPACING_RE.search(value):
                return True
    return False


def _has_unprotected_semantic_elements(src_dir: Path) -> bool:
    for tsx_file in src_dir.rglob('*.tsx'):
        content = tsx_file.read_text(encoding='utf-8', errors='ignore')
        for m in _SEMANTIC_TAG_RE.finditer(content):
            attrs = m.group(2)
            cm = _CLASSNAME_VALUE_RE.search(attrs)
            if cm:
                cls_val = cm.group(1) or cm.group(2) or cm.group(3) or cm.group(4) or ''
                if _EXPLICIT_SPACING_RE.search(cls_val):
                    continue
            return True
    return False


def check_css_reset_issue(project_dir: Path) -> bool:
    css_file = project_dir / 'src' / 'index.css'
    src_dir = project_dir / 'src'
    if not css_file.exists() or not src_dir.exists():
        return False
    css_text = css_file.read_text(encoding='utf-8', errors='ignore')
    if not _css_has_star_reset(css_text):
        return False
    return _uses_tailwind_spacing(src_dir) and _has_unprotected_semantic_elements(src_dir)
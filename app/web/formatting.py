"""Renders a message's raw text the way real model UIs (ChatGPT, Claude,
Gemini) show a generated response - headings, lists, tables and inline
formatting turned into real markup, and fenced code blocks turned into a
syntax-highlighted panel with a language label and a copy button - instead
of one flat `white-space: pre-wrap` blob of text.

Everything here runs server-side with libraries already in requirements.txt
(Markdown + Pygments), consistent with the rest of the web UI: no CDN
scripts, no client-side markdown parser. Output always passes through `nh3`
before it reaches a template, because the input is a stored chat message,
not code we wrote - it can contain anything a user typed (including raw
`<script>`) or, in principle, anything a provider echoed back.
"""
from __future__ import annotations

import html
import re
from typing import Match

import markdown as _markdown
import nh3
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.lexers.special import TextLexer
from pygments.util import ClassNotFound

# Matches a ``` fenced block sitting on its own lines, capturing the
# optional language tag and the code between the fences. Pulled out and
# highlighted *before* the Markdown pass so Markdown never sees (and can't
# mangle) the code's own contents.
_FENCE_RE = re.compile(
    r"^ {0,3}```([\w+-]*)[ \t]*\n(.*?)\n {0,3}```[ \t]*$",
    re.DOTALL | re.MULTILINE,
)

# Private-use-area markers: won't collide with anything a person or a model
# would plausibly type, and Markdown treats them as inert plain text.
_PLACEHOLDER = "CODEBLOCK{index}"

_MD = _markdown.Markdown(extensions=["fenced_code", "tables", "sane_lists", "nl2br"])
_PYGMENTS_FORMATTER = HtmlFormatter(nowrap=True)

_ALLOWED_TAGS = {
    "p", "br", "hr", "strong", "em", "del", "sup", "sub",
    "code", "pre", "blockquote",
    "ul", "ol", "li",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "thead", "tbody", "tr", "th", "td",
    "a", "span", "div", "button",
}
_ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
    "code": {"class"},
    "pre": {"class"},
    "span": {"class"},
    "div": {"class"},
    "button": {"class", "type", "aria-label"},
    "th": {"align"},
    "td": {"align"},
}


def _lexer_for(lang: str, code: str):
    if lang:
        try:
            return get_lexer_by_name(lang)
        except ClassNotFound:
            pass
    try:
        return guess_lexer(code)
    except ClassNotFound:
        return TextLexer()


def _highlighted_block(lang: str, code: str) -> str:
    lang = lang.strip().lower()
    lexer = _lexer_for(lang, code)
    label = lang or (lexer.aliases[0] if lexer.aliases else "text")
    highlighted = highlight(code, lexer, _PYGMENTS_FORMATTER)
    return (
        '<div class="code-block">'
        '<div class="code-block__bar">'
        f'<span class="code-block__lang">{html.escape(label)}</span>'
        '<button type="button" class="code-block__copy" aria-label="Copy code">Copy</button>'
        "</div>"
        f'<pre class="code-block__pre"><code>{highlighted}</code></pre>'
        "</div>"
    )


def render_message(content: str) -> str:
    """Convert one message's stored text into sanitized, chat-app-style HTML."""
    if not content:
        return ""

    blocks: dict[str, str] = {}

    def _stash(match: Match) -> str:
        key = _PLACEHOLDER.format(index=len(blocks))
        blocks[key] = _highlighted_block(match.group(1), match.group(2))
        return key

    without_fences = _FENCE_RE.sub(_stash, content)

    _MD.reset()
    body_html = _MD.convert(without_fences)

    for key, block_html in blocks.items():
        body_html = body_html.replace(f"<p>{key}</p>", block_html)
        body_html = body_html.replace(key, block_html)

    return nh3.clean(
        body_html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        link_rel="noopener noreferrer nofollow",
    )

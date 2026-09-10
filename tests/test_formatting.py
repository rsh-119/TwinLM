"""Tests for app/web/formatting.py - turning a stored message's raw text
into sanitized, chat-app-style HTML. No HTTP/DB involved."""
from app.web.formatting import render_message


def test_plain_text_is_wrapped_in_a_paragraph():
    assert render_message("just talking") == "<p>just talking</p>"


def test_empty_content_renders_to_empty_string():
    assert render_message("") == ""


def test_common_markdown_is_converted():
    html = render_message("**bold**, *italic*, and `inline code`")

    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html
    assert "<code>inline code</code>" in html


def test_lists_and_headings_render():
    html = render_message("## Heading\n\n- one\n- two")

    assert "<h2>Heading</h2>" in html
    assert "<ul>" in html
    assert "<li>one</li>" in html


def test_tables_render():
    html = render_message("| a | b |\n|---|---|\n| 1 | 2 |")

    assert "<table>" in html
    assert "<th>a</th>" in html


def test_fenced_code_block_gets_a_language_label_and_copy_button():
    html = render_message('```python\ndef add(a, b):\n    return a + b\n```')

    assert 'class="code-block"' in html
    assert '<span class="code-block__lang">python</span>' in html
    assert 'class="code-block__copy"' in html
    # Pygments should have tokenized the keyword, not left it as raw text.
    assert "def" in html and "<span" in html


def test_fenced_code_block_without_a_language_falls_back_to_a_guess():
    html = render_message("```\nplain text block\n```")

    assert "plain text block" in html
    assert 'class="code-block__lang"' in html


def test_code_block_content_is_not_run_through_markdown():
    """Markdown syntax inside a fenced block should show up literally, not
    get turned into HTML - e.g. a `#` in a code comment isn't a heading."""
    html = render_message("```\n# not a heading\n```")

    assert "<h1>" not in html
    assert "not a heading" in html


def test_script_tags_are_stripped():
    html = render_message("hello <script>alert('xss')</script> world")

    assert "<script" not in html
    assert "alert" not in html
    assert "hello" in html and "world" in html


def test_event_handler_attributes_are_stripped():
    html = render_message('<img src="x" onerror="alert(1)">')

    assert "onerror" not in html


def test_javascript_links_are_stripped():
    html = render_message("[click me](javascript:alert(1))")

    assert "javascript:" not in html


def test_ordinary_links_survive_with_safe_rel():
    html = render_message("[docs](https://example.com)")

    assert 'href="https://example.com"' in html
    assert "noopener" in html

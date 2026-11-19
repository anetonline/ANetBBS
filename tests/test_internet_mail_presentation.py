"""Shared WebUI/terminal display formatting for Internet Mail bodies."""


def test_display_lines_collapses_excess_blank_space_without_rewriting_content():
    from anetbbs.mail.presentation import display_lines

    assert display_lines('\nhello\n\n\n\nthere\n\n') == [
        ('hello', False), ('', False), ('there', False)]


def test_display_lines_marks_quoted_text_and_its_reply_intro():
    from anetbbs.mail.presentation import display_lines

    assert display_lines('Reply\n\nSender wrote:\n\n> original') == [
        ('Reply', False), ('', False), ('Sender wrote:', True),
        ('', False), ('> original', True)]


def test_web_render_escapes_untrusted_content_and_styles_quotes():
    from anetbbs.mail.presentation import render_body_html

    rendered = str(render_body_html('Reply\n\n> <script>alert(1)</script>'))
    assert '<span class="mail-quoted">&gt; &lt;script&gt;' in rendered
    assert '<script>' not in rendered

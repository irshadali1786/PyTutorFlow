from app.service.messages import split_message
from app.service.normalize import clean_telegram_code, extract_choice, notes_footer


def test_curly_quotes_are_fixed():
    code, notes = clean_telegram_code("print(“Hello World”)")
    assert code == 'print("Hello World")' and notes == ["curly_quotes"]
    assert "smart punctuation" in notes_footer(notes)


def test_markdown_fences_are_removed():
    for text in ('```python\nprint("hi")\n```', '```\nprint("hi")\n```', '```print("hi")```', '`print("hi")`', '```py\nprint("hi")'):
        code, notes = clean_telegram_code(text)
        assert code == 'print("hi")', text
        assert "code_fence" in notes


def test_odd_spaces_zero_width_and_indent():
    assert clean_telegram_code("print(​'a') ")[0] == "print('a')"
    code, _ = clean_telegram_code("    x = 1\n    print(x)")
    assert code == "x = 1\nprint(x)"
    assert clean_telegram_code("a\r\nb")[0] == "a\nb"


def test_ellipsis_and_plain_code_untouched():
    assert clean_telegram_code("print('wait…')")[0] == "print('wait...')"
    code, notes = clean_telegram_code('print("Hello World")')
    assert code == 'print("Hello World")' and notes == []


def test_extract_choice():
    for raw in ("b", "B", "b)", "(b)", "B) Writing instructions", " **b** ", "b.\nthanks"):
        assert extract_choice(raw) == "b", raw
    assert extract_choice("Python") == "Python"          # not mistaken for a letter


def test_split_message():
    assert split_message("short") == ["short"]
    long = "\n\n".join(["x" * 900] * 8)
    parts = split_message(long, limit=2000)
    assert len(parts) > 1 and all(len(p) <= 2000 for p in parts)
    assert split_message("y" * 5000, limit=2000) and all(len(p) <= 2000 for p in split_message("y" * 5000, limit=2000))

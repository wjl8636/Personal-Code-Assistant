import json


def _split_row(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [cell.strip() for cell in line.split("|")]


def _is_separator(cells):
    return all(cell and set(cell) <= set("-: ") for cell in cells)


def parse_markdown_table(text):
    rows = [_split_row(line) for line in text.splitlines() if line.strip()]
    if not rows:
        return []

    header = rows[0]
    body = rows[1:]
    if body and _is_separator(body[0]):
        body = body[1:]

    return [
        {key: (row[i] if i < len(row) else "") for i, key in enumerate(header)}
        for row in body
    ]


if __name__ == "__main__":
    sample = """| name | age |
| --- | --- |
| Alice | 30 |
| Bob | 25 |"""
    print(json.dumps(parse_markdown_table(sample), indent=2))

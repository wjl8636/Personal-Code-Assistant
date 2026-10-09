import json


def parse_markdown_table(text):
    rows = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        rows.append(cells)

    if not rows:
        return []

    header = rows[0]
    body = [r for r in rows[1:] if not all(set(c) <= set("-: ") for c in r)]

    result = []
    for row in body:
        result.append({header[i]: row[i] for i in range(min(len(header), len(row)))})
    return result


if __name__ == "__main__":
    sample = """| name | age |
| --- | --- |
| Alice | 30 |
| Bob | 25 |"""
    print(json.dumps(parse_markdown_table(sample), indent=2))

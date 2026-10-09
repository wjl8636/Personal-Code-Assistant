import json


def parse_markdown_table(text):
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in text.strip().splitlines()
        if line.strip()
    ]
    if not rows:
        return []
    headers = rows[0]
    data = rows[2:] if len(rows) > 1 and set(rows[1][0]) <= set("-: ") else rows[1:]
    return [dict(zip(headers, row)) for row in data]


if __name__ == "__main__":
    table = """| name | age |
| --- | --- |
| Alice | 30 |
| Bob | 25 |"""
    print(json.dumps(parse_markdown_table(table), indent=2))

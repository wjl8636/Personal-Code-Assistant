import json

from md_table import parse_markdown_table

TABLE = """| city | population |
| --- | --- |
| Tokyo | 37M |
| NY | 8M |"""

print(json.dumps(parse_markdown_table(TABLE)))

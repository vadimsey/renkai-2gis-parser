"""Common worker file helpers. The collection engine is hybrid_parser."""
import json
import asyncio
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote


def write_json(path, data):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    for attempt in range(8):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(.04 * (attempt + 1))


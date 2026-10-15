import os

os.environ.setdefault("OSCAR_MEMORY", "keyword")  # tests never start Cognee (no LLM calls, no local graph)

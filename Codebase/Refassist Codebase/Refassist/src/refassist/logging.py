import os, json, logging

LOG_LEVEL = os.getenv("IEEE_REF_LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO), format="%(message)s")
logger = logging.getLogger("refassist")

def jlog(**kw):
    try:
        print(json.dumps(kw, ensure_ascii=False))
    except Exception:
        print(str(kw))

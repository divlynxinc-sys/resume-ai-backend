import json
import sys

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_ADDRESS_SPACE = 192 * 1024 * 1024


def parse_input(extension: str, include_review: bool = False):
    if sys.platform.startswith("linux"):
        import resource

        for limit, ceiling in ((resource.RLIMIT_AS, MAX_ADDRESS_SPACE), (resource.RLIMIT_CPU, 8)):
            _, hard = resource.getrlimit(limit)
            bound = ceiling if hard == resource.RLIM_INFINITY else min(ceiling, hard)
            resource.setrlimit(limit, (bound, bound))

    from app.utils.resume_parser import DocumentLimitError, EmptyDocumentError, parse_uploaded_resume

    data = sys.stdin.buffer.read(MAX_DOCUMENT_BYTES + 1)
    if len(data) > MAX_DOCUMENT_BYTES:
        return {"error": "upload_too_large"}
    try:
        return {"content": parse_uploaded_resume(data, "resume" + extension, include_review=include_review)}
    except EmptyDocumentError:
        return {"error": "no_extractable_text"}
    except (DocumentLimitError, MemoryError):
        return {"error": "document_limit_exceeded"}
    except Exception:
        return {"error": "unreadable_document"}


if __name__ == "__main__":
    print(json.dumps(parse_input(sys.argv[1], len(sys.argv) == 3 and sys.argv[2] == "review"), ensure_ascii=True))

from __future__ import annotations

from app.external.chroma_client import ChromaClient, FILING_COLLECTION_NAME


def main() -> None:
    client = ChromaClient()
    client.delete_collection(FILING_COLLECTION_NAME)
    print({"collection": FILING_COLLECTION_NAME, "deleted": True})


if __name__ == "__main__":
    main()

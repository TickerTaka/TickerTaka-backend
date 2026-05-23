from __future__ import annotations

import json

from app.external.dart.corp_code import CorpCodeProvider


def main() -> None:
    provider = CorpCodeProvider()
    mapping = provider.load_mapping(force_refresh=True)
    print(json.dumps({"count": len(mapping), "cache_path": str(provider.cache_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

# Native authority dependencies

These sources are preserved unmodified and are built offline. [manifest.json](manifest.json) records source URLs, exact versions and per-file SHA-256 values. Verify them with `python -m tools.check_native_dependencies`.

- SQLite 3.54.1 `sqlite3.c` and `sqlite3.h`, from the official 2026 amalgamation archive. Its archive SHA3-256 was checked against the official download page before extracting these files. The upstream public-domain notices remain in both files. [Official download](https://www.sqlite.org/download.html), [copyright statement](https://www.sqlite.org/copyright.html).
- nlohmann/json 3.12.0, official single header. Its MIT license and the header notices are retained in `nlohmann/LICENSE.MIT` and `nlohmann/json.hpp`. [Official release](https://github.com/nlohmann/json/releases/tag/v3.12.0).

These third-party rights apply to their own files and do not relicense existing DaOS code or other project material. No source from the existing DaOS repositories is introduced here.

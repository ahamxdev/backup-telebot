import json
import signal
import sys
from types import SimpleNamespace

from domain.enums import TLSMode
from pipeline.acquisition import _mssql_backup
from pipeline.errors import PipelineError


class _MSSQLStop(Exception):
    pass


def _stop(_signum: int, _frame: object) -> None:
    raise _MSSQLStop


def main() -> int:
    signal.signal(signal.SIGTERM, _stop)
    try:
        request = json.loads(sys.stdin.buffer.read())
        password = request.pop("password")
        request["tls_mode"] = TLSMode(request["tls_mode"])
        metadata = _mssql_backup(SimpleNamespace(**request), password)  # type: ignore[arg-type]
        response = {"ok": True, "metadata": metadata}
    except PipelineError as exc:
        response = {"ok": False, "code": exc.code, "message": str(exc)}
    except Exception:
        response = {
            "ok": False,
            "code": "MSSQL_BACKUP_FAILED",
            "message": "MSSQL backup process failed",
        }
    sys.stdout.write(json.dumps(response, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

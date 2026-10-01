"""Local SMTP catcher for testing licence emails.

Accepts any username/password and writes every message to a folder instead of
delivering it. Point the licence service at it:

    BYTEPROOF_SMTP_HOST=127.0.0.1 BYTEPROOF_SMTP_PORT=1025 \
    BYTEPROOF_SMTP_USER=dev BYTEPROOF_SMTP_PASSWORD=dev \
    BYTEPROOF_SMTP_TLS=false

Requires ``aiosmtpd`` (a test-only dependency):

    pip install aiosmtpd
    python scripts/dev_smtp_catcher.py --directory /tmp/byteproof-mail
"""

from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path

from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult


class AnyAuth:
    def __call__(self, server, session, envelope, mechanism, auth_data):
        return AuthResult(success=True)


class FileHandler:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    async def handle_DATA(self, server, session, envelope):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = self.directory / f"{stamp}-{envelope.rcpt_tos[0]}.eml".replace("/", "_")
        path.write_bytes(envelope.content)
        print(f"[catcher] {envelope.mail_from} -> {envelope.rcpt_tos}: {path}")
        return "250 Message accepted for delivery"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1025)
    parser.add_argument("--directory", default="/tmp/byteproof-mail")
    args = parser.parse_args()

    controller = Controller(
        FileHandler(Path(args.directory)),
        hostname=args.host,
        port=args.port,
        authenticator=AnyAuth(),
        auth_require_tls=False,
        tls_context=None,
    )
    controller.start()
    print(f"[catcher] listening on {args.host}:{args.port} -> {args.directory}")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        controller.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

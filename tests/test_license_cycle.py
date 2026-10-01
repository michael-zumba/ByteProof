"""The app half of the license cycle, over real HTTP.

A tiny stub of the license service runs on localhost, so the desktop client
is driven through urllib exactly as in production: purchase key -> activate ->
use -> deactivate -> released -> revoked, plus the tamper cases that only the
app can enforce (a copied license file, a forged signed key).
"""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Self

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_license_generator():
    path = os.path.join(PROJECT_ROOT, "tools", "generate_license.py")
    if not os.path.exists(path):
        pytest.skip("tools/generate_license.py is not in this checkout")
    spec = importlib.util.spec_from_file_location("generate_license_cycle", path)
    assert spec is not None and spec.loader is not None
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    return generator


class _StubService:
    """In-memory stand-in for the license service, over real HTTP."""

    def __init__(self, generator: Any, purchase_key: str, email: str) -> None:
        self.generator = generator
        self.purchase_key = purchase_key
        self.email = email
        self.activations: dict[str, str] = {}  # machine_fp -> signed licence
        self.revoked = False
        self.released: set[str] = set()
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.deactivate_calls = 0
        self.portal_requests: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:  # keep pytest quiet
                pass

            def _read(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(length) or b"{}")

            def _send(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                payload = self._read()
                outer.requests.append((self.path, payload))
                if self.path == "/api/byteproof/activate":
                    outer.handle_activate(payload, self._send)
                elif self.path == "/api/byteproof/validate":
                    outer.handle_validate(payload, self._send)
                elif self.path == "/api/byteproof/deactivate":
                    outer.handle_deactivate(payload, self._send)
                elif self.path == "/api/byteproof/portal/request":
                    outer.portal_requests.append(str(payload.get("email") or ""))
                    self._send(200, {"ok": True, "message": "Portal link sent."})
                else:
                    self._send(404, {"detail": "Not found."})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> Self:
        self.thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    # -- service behaviour --------------------------------------------------

    def handle_activate(
        self, payload: dict[str, Any], send: Any
    ) -> None:
        key = str(payload.get("key") or "").strip().upper()
        machine = str(payload.get("machine_fingerprint") or "")
        if key != self.purchase_key.upper():
            send(404, {"detail": "We could not find that license key."})
            return
        if self.revoked:
            send(
                403,
                {"detail": "This license was refunded or revoked and is no longer valid."},
            )
            return
        if machine in self.activations:
            signed = self.activations[machine]
        elif len(self.activations) >= 2:
            send(
                403,
                {
                    "detail": (
                        "This license has reached its limit of 2 computers. "
                        "Deactivate another computer first."
                    )
                },
            )
            return
        else:
            signed = self.generator.generate_license_key(
                self.email, "unlimited", machine
            )
            self.activations[machine] = signed
        send(
            200,
            {
                "ok": True,
                "license_key": signed,
                "key": self.purchase_key,
                "email": self.email,
                "device_count": len(self.activations),
                "device_limit": 2,
            },
        )

    def handle_validate(self, payload: dict[str, Any], send: Any) -> None:
        machine = str(payload.get("machine_fingerprint") or "")
        if self.revoked:
            send(
                200,
                {
                    "ok": True,
                    "valid": False,
                    "revoked": True,
                    "reason": "revoked",
                    "error": "This license was refunded or revoked.",
                },
            )
        elif machine not in self.activations:
            send(
                200,
                {
                    "ok": False,
                    "valid": False,
                    "revoked": False,
                    "reason": "not_activated",
                    "error": "This computer is not activated for this license key.",
                },
            )
        else:
            send(
                200,
                {
                    "ok": True,
                    "valid": True,
                    "revoked": False,
                    "device_count": len(self.activations),
                    "device_limit": 2,
                },
            )

    def handle_deactivate(self, payload: dict[str, Any], send: Any) -> None:
        machine = str(payload.get("machine_fingerprint") or "")
        self.deactivate_calls += 1
        self.activations.pop(machine, None)
        self.released.add(machine)
        send(200, {"ok": True})


_MACHINE_DIRS: dict[str, str] = {}


def _use_machine(name: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """Isolate license storage and pretend to be `name`."""
    from src import activation, license_api, licensing

    tmp = _MACHINE_DIRS.setdefault(
        name, tempfile.mkdtemp(prefix=f"bp-cycle-{name}-")
    )
    monkeypatch.setattr(
        licensing, "_get_license_path", lambda: os.path.join(tmp, "license.json")
    )
    monkeypatch.setattr(licensing, "_secure_store_set", lambda _value: None)
    monkeypatch.setattr(licensing, "_secure_store_get", lambda: None)
    monkeypatch.setattr(licensing, "_secure_store_delete", lambda: None)
    monkeypatch.setattr(licensing, "_get_machine_fingerprint", lambda: name)
    monkeypatch.setattr(activation, "_get_machine_fingerprint", lambda: name)
    monkeypatch.setattr(license_api, "machine_fingerprint", lambda: name)
    return tmp


def test_license_cycle_activate_use_release_and_revoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src import activation, license_api, licensing

    generator = _load_license_generator()
    purchase_key = "BYTP-CYCLE-0000-0000-0000"
    with _StubService(generator, purchase_key, "cycle@example.com") as service:
        monkeypatch.setattr(license_api, "LICENSE_API_URL", service.url)

        # --- before buying: trial, then free mode ------------------------
        _use_machine("cycle-laptop", monkeypatch)
        assert licensing.get_access_status()["tier"] == "trial"

        # --- activate ----------------------------------------------------
        result = activation.activate_with_key(purchase_key.lower())
        assert result["ok"], result
        assert licensing.is_licensed()
        info = licensing.get_license_info()
        assert info["provider"] == "stripe"
        assert info["purchase_key"] == purchase_key
        assert info["email"] == "cycle@example.com"
        assert licensing.get_access_status()["tier"] == "licensed"

        # Usage: a licensed customer is never gated by the daily free cap.
        for _ in range(10):
            licensing.record_proofread_usage()
        access = licensing.get_access_status()
        assert access["tier"] == "licensed"
        assert access["licensed"] is True

        # --- online validation is clean ----------------------------------
        assert activation.apply_remote_validation(
            activation.validate_license_remote()
        )["kind"] == "none"

        # --- second computer, and the third is refused -------------------
        _use_machine("cycle-desktop", monkeypatch)
        assert activation.activate_with_key(purchase_key)["ok"]
        _use_machine("cycle-tablet", monkeypatch)
        third = activation.activate_with_key(purchase_key)
        assert not third["ok"] and "limit of 2 computers" in third["error"]

        # --- releasing this computer in the portal stops it working ------
        _use_machine("cycle-desktop", monkeypatch)
        assert activation.apply_remote_validation(
            activation.validate_license_remote()
        )["kind"] == "none"
        service.activations.pop("cycle-desktop")  # the customer used the portal
        outcome = activation.apply_remote_validation(
            activation.validate_license_remote()
        )
        assert outcome["kind"] == "deactivated"
        assert not licensing.is_licensed()
        assert licensing.get_access_status()["tier"] in ("trial", "free")

        # --- deactivating from the app frees the seat and tells the server
        _use_machine("cycle-laptop", monkeypatch)
        assert activation.deactivate_license()["ok"]
        assert not licensing.is_licensed()
        assert service.deactivate_calls >= 1
        assert "cycle-laptop" in service.released

        # --- a refund locks the app on the next check --------------------
        _use_machine("cycle-tablet", monkeypatch)
        assert activation.activate_with_key(purchase_key)["ok"]
        service.revoked = True
        outcome = activation.apply_remote_validation(
            activation.validate_license_remote()
        )
        assert outcome["kind"] == "revoked"
        assert not licensing.is_licensed()
        blocked = activation.activate_with_key(purchase_key)
        assert not blocked["ok"] and "refunded" in blocked["error"].lower()


def test_copied_and_forged_license_files_do_not_unlock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src import activation, license_api, licensing

    generator = _load_license_generator()
    purchase_key = "BYTP-CYCLE-1111-1111-1111"
    with _StubService(generator, purchase_key, "tamper@example.com") as service:
        monkeypatch.setattr(license_api, "LICENSE_API_URL", service.url)
        folder = _use_machine("owner-machine", monkeypatch)
        assert activation.activate_with_key(purchase_key)["ok"]
        with open(os.path.join(folder, "license.json"), encoding="utf-8") as fh:
            stored = fh.read()

        # The same file on different hardware must not validate.
        _use_machine("stolen-machine", monkeypatch)
        with open(os.path.join(folder, "license.json"), "w", encoding="utf-8") as fh:
            fh.write(stored)
        # (the folder was re-pointed for the new machine; copy the file there)
        target = licensing._get_license_path()
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(stored)
        assert licensing.is_licensed() is False

        # A hand-edited payload with a made-up signed key must not validate.
        forged = json.loads(stored)
        forged["machine_fp"] = "stolen-machine"
        forged["key"] = "Zm9yZ2Vk|dW5saW1pdGVk|" + forged["key"].split("|")[2] + "|AAAA"
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(forged, fh)
        assert licensing.is_licensed() is False

        # Moving the *purchase key* to a third computer is refused by the
        # server once both seats are taken.
        _use_machine("machine-2", monkeypatch)
        assert activation.activate_with_key(purchase_key)["ok"]
        _use_machine("machine-3", monkeypatch)
        assert not activation.activate_with_key(purchase_key)["ok"]

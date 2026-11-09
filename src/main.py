import argparse
import os
import sys
import time
import traceback

from PyQt6.QtGui import QColor, QIcon, QPalette
from PyQt6.QtWidgets import QApplication

from config.deepseek_config import DEFAULT_MAX_OUTPUT_CHAT

from .gui import ProofreaderApp

# Use relative imports assuming this is run as a module
from .settings import get_app_support_dir, load_runtime_settings, resource_path


def _install_exception_hook() -> None:
    """Keep unhandled exceptions from aborting the Qt app.

    PyQt6 calls qFatal() and aborts when a Python exception escapes a Qt slot
    unless sys.excepthook is replaced. This hook logs the full traceback to
    the ByteProof support folder and lets the app keep running.
    """

    def _hook(exc_type, exc_value, exc_tb) -> None:
        try:
            log_dir = get_app_support_dir()
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, "error.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(
                    f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n"
                )
                traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
        except Exception:
            pass
        traceback.print_exception(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Proofread selected text in the active Microsoft Word document.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=DEFAULT_MAX_OUTPUT_CHAT,
        help="Maximum output tokens requested from the AI API.",
    )
    parser.add_argument(
        "--capture-test",
        action="store_true",
        help="Print the detected frontmost app and selected text, then exit.",
    )
    return parser.parse_args()


def another_instance_is_running() -> bool:
    """Whether a second copy of ByteProof is already running (macOS).

    Two instances would both watch the same selection and both paste over it,
    so the owner could get doubled edits. LaunchServices stops a second launch
    of the *same* bundle, but a copy somewhere else - the ``previous-versions``
    folder the app keeps after an update is exactly that - is a different app
    as far as macOS is concerned. Only bundled instances count: a run from
    source is not this bundle and is left alone.
    """
    if not getattr(sys, "frozen", False):
        return False
    try:
        from AppKit import NSWorkspace

        mine = os.getpid()
        for candidate in NSWorkspace.sharedWorkspace().runningApplications():
            try:
                bundle = str(candidate.bundleIdentifier() or "")
                pid = int(candidate.processIdentifier())
            except Exception:
                continue
            if bundle == "nz.co.bytemind.byteproof" and pid != mine:
                return True
    except Exception:
        return False
    return False


def main() -> int:
    _install_exception_hook()
    args = parse_args()
    if args.capture_test:
        import json

        from .generic_editing import capture_diagnostics
        result = capture_diagnostics()
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return 0
    if another_instance_is_running():
        # Say so plainly instead of running a second watcher: two of them
        # would fight over the same selection.
        try:
            from AppKit import NSRunningApplication

            for candidate in NSRunningApplication.runningApplicationsWithBundleIdentifier_(
                "nz.co.bytemind.byteproof"
            ):
                if int(candidate.processIdentifier()) != os.getpid():
                    candidate.activateWithOptions_(0)
                    break
        except Exception:
            pass
        print("ByteProof is already running (menu bar). This copy will exit.")
        return 0
    runtime_settings = load_runtime_settings()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("ByteProof")
    app.setApplicationDisplayName("ByteProof")
    app.setOrganizationName("ByteMind Ltd")

    # Force the ByteProof light palette so Windows dark mode cannot turn
    # unstyled panels dark. macOS and Windows then look identical.
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#F7F4F0"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#292524"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#FFFFFF"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#F1EDE8"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#292524"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#FFFFFF"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#292524"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#FFFFFF"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#292524"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#1A3A2A"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    app.setPalette(palette)

    icon_path = resource_path(os.path.join("logo", "logo.svg"))
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    window = ProofreaderApp(args.max_tokens, runtime_settings)
    window.show()

    # Handle byteproof://activate deep links (opened from a fulfilment email).
    for arg in sys.argv:
        if arg.startswith("byteproof://"):
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(800, lambda a=arg: window._start_activation("url", a))
            break

    return app.exec()

if __name__ == "__main__":
    sys.exit(main())

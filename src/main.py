import argparse
import os
import sys

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from config.deepseek_config import DEFAULT_MAX_OUTPUT_CHAT

from .gui import ProofreaderApp

# Use relative imports assuming this is run as a module
from .settings import load_runtime_settings, resource_path


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

def main() -> int:
    args = parse_args()
    if args.capture_test:
        import json

        from .generic_editing import capture_diagnostics
        result = capture_diagnostics()
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return 0
    runtime_settings = load_runtime_settings()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("ByteProof")
    app.setApplicationDisplayName("ByteProof")
    app.setOrganizationName("ByteMind Ltd")

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

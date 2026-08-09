# pyright: reportAttributeAccessIssue=false
import copy
import difflib
import os
import platform
import subprocess
import threading
import time
import webbrowser
from typing import Any

from PyQt6.QtCore import (
    QEvent,
    QObject,
    QPropertyAnimation,
    QRectF,
    Qt,
    QThread,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QFont,
    QFontDatabase,
    QIcon,
    QKeySequence,
    QPainter,
    QTextCharFormat,
)
from PyQt6.QtSvgWidgets import QSvgWidget
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .activation import (
    activate_from_url,
    activate_with_email,
    deactivate_license,
    register_url_scheme,
    validate_license_remote,
)
from .app_version import check_for_updates, download_update
from .autostart import set_launch_at_login
from .generic_editing import get_generic_editor, normalize_selection_text
from .licensing import (
    ensure_trial_started,
    get_access_status,
    get_license_info,
    get_trial_status,
    is_licensed,
    record_proofread_usage,
)
from .local_model import (
    MODEL_CATALOG,
    DownloadCancelledError,
    detect_hardware,
    ensure_local_model,
    get_model,
    is_model_installed,
    local_server_info,
    recommend_model,
    remove_model,
    resolve_model_id,
    start_local_server,
    stop_local_server,
)
from .logic import (
    TABLE_SKIPPED_STATUS,
    _find_protected_spans,
    apply_corrections_with_diff,
    polish_selection_once,
    proofread_selection_once,
    test_provider_connection,
)
from .settings import (
    APP_NAME,
    APP_VERSION,
    COMPANY_NAME,
    LOCAL_MODEL_PROVIDER,
    PRODUCT_URL,
    PROVIDERS,
    STRIPE_PAYMENT_URL,
    SUPPORT_EMAIL,
    resource_path,
    save_runtime_settings,
)
from .sound import play_start_sound
from .word_integration import get_word_integration


def _ui_font(size: int, weight: QFont.Weight | None = None) -> QFont:
    """Return the platform default font at the requested size/weight."""
    font = QFont()
    font.setPointSize(size)
    if weight is not None:
        font.setWeight(weight)
    return font


def _mono_font(size: int = 12) -> QFont:
    """Return the platform's fixed-width font."""
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPointSize(size)
    return font


def model_size_label(model: dict[str, Any]) -> str:
    return f"{model['size_bytes'] / (1024 ** 3):.1f} GB"


def _format_bytes(size: int) -> str:
    """Format a byte count as a human-readable size (e.g. 1.24 GB)."""
    size = max(0, int(size))
    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024
        if value < 1024 or unit == "TB":
            return f"{value:.2f} {unit}"
    return f"{value:.2f} TB"


def open_purchase_url(parent: QWidget | None = None) -> None:
    """Open the Stripe payment page, or explain that payments are pending."""
    if "REPLACE_WITH" in STRIPE_PAYMENT_URL:
        QMessageBox.information(
            parent,
            "Purchases Coming Soon",
            "ByteProof payments are being set up with Stripe.\n\n"
            "Until then, please contact ByteMind Ltd at bytemind.nz@gmail.com "
            "if you would like to purchase a license.",
        )
    else:
        webbrowser.open(STRIPE_PAYMENT_URL)


def evaluate_apply_verification(
    original: str,
    corrected: str,
    after: str,
) -> tuple[bool, str]:
    """Decide whether a pasted result should be treated as applied.

    Apps like Outlook and Mail often reformat text on paste (smart quotes,
    spacing, etc.), so a perfect read-back match is not required. Only an
    unchanged original selection counts as a failed paste.
    """
    normalized_after = normalize_selection_text(after)
    normalized_corrected = normalize_selection_text(corrected)
    normalized_original = normalize_selection_text(original)
    if normalized_after == normalized_corrected:
        return True, ""
    if normalized_after == normalized_original:
        return False, "ORIGINAL_STILL_SELECTED"
    return True, ""


def is_update_dismissed(remote_version: str, settings: dict[str, Any]) -> bool:
    """True when the user already dismissed this exact update version."""
    return bool(remote_version) and settings.get("general", {}).get("skipped_update_version") == remote_version


class WaveformBars(QWidget):
    """Animated recorder-style equalizer bars (like a mini voice recorder)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(30, 18)
        self._phase = 0
        self._color = QColor("#F59E0B")
        self._timer = QTimer(self)
        self._timer.setInterval(90)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._phase = 0
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def _tick(self) -> None:
        self._phase += 1
        self.update()

    def paintEvent(self, a0: Any) -> None:  # pyright: ignore[reportAny]
        import math

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        count = 5
        gap = 3
        bar_w = (self.width() - gap * (count - 1)) / count
        for i in range(count):
            phase = (self._phase * 0.9 + i * 0.9) % (math.pi * 2)
            height = 5 + int(abs(math.sin(phase)) * (self.height() - 5))
            x = i * (bar_w + gap)
            y = (self.height() - height) / 2
            painter.drawRoundedRect(QRectF(x, y, bar_w, height), 1.5, 1.5)


class ToastNotification(QFrame):
    """VoiceInk-style 'mini recorder' pill shown while proofreading.

    While a task runs it is a dark pill at the bottom-centre of the screen with
    animated equalizer bars, live status text, and a running timer. When the
    task finishes it switches to a green/red/amber result state and fades away
    automatically.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ToastNotification")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet(
            "#ToastNotification { background-color: rgba(17, 24, 39, 235); "
            "border-radius: 22px; }"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(10)

        self.waveform = WaveformBars()
        layout.addWidget(self.waveform)

        self.dot = QLabel()
        self.dot.setFixedSize(12, 12)
        self.dot.setStyleSheet("border-radius: 6px; background-color: #F59E0B;")
        layout.addWidget(self.dot)
        self.dot.hide()

        self.label = QLabel()
        self.label.setStyleSheet(
            "color: #FFFFFF; font-size: 13px; font-weight: 600; background: transparent;"
        )
        layout.addWidget(self.label)

        self.time_label = QLabel("0:00")
        self.time_label.setStyleSheet(
            "color: rgba(255, 255, 255, 180); font-size: 12px; "
            "font-family: Menlo, Consolas, monospace; background: transparent;"
        )
        layout.addWidget(self.time_label)
        self.time_label.hide()

        self.setWindowOpacity(0.0)
        self._fade_animation: QPropertyAnimation | None = None
        self._elapsed = 0
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._fade_out)
        self._processing = False

    def is_processing(self) -> bool:
        return self._processing

    def update_message(self, message: str) -> None:
        """Update the pill text while a task is running."""
        self.label.setText(message)
        self.adjustSize()
        self._position_on_screen()

    def show_processing(self, message: str, pulse_color: str = "#F59E0B") -> None:
        """Show the recorder-style pill; stays visible until complete()."""
        self._processing = True
        self._elapsed = 0
        self.label.setText(message)
        self.dot.hide()
        self.time_label.setText("0:00")
        self.time_label.show()
        self.waveform.set_color(pulse_color)
        self.waveform.show()
        self.waveform.start()
        self._elapsed_timer.start()
        self._show_pill()

    def _tick_elapsed(self) -> None:
        self._elapsed += 1
        minutes, seconds = divmod(self._elapsed, 60)
        self.time_label.setText(f"{minutes}:{seconds:02d}")

    def complete(
        self,
        message: str,
        kind: str = "success",
        duration_ms: int = 3000,
    ) -> None:
        """Switch to a finished state (green/amber/red dot) and auto-hide."""
        self._processing = False
        self.waveform.stop()
        self.waveform.hide()
        self._elapsed_timer.stop()
        self.time_label.hide()
        self.dot.show()
        if kind == "error":
            self.dot.setStyleSheet("border-radius: 6px; background-color: #F87171;")
        elif kind == "warning":
            self.dot.setStyleSheet("border-radius: 6px; background-color: #F59E0B;")
        else:
            self.dot.setStyleSheet("border-radius: 6px; background-color: #4ADE80;")
        self.label.setText(message)
        self.adjustSize()
        self._position_on_screen()
        self.setWindowOpacity(1.0)
        self.show()
        self.raise_()
        self._hide_timer.start(duration_ms)

    def show_message(
        self,
        message: str,
        kind: str = "success",
        duration_ms: int = 3000,
    ) -> None:
        self.complete(message, kind=kind, duration_ms=duration_ms)

    def _show_pill(self) -> None:
        self.adjustSize()
        self._position_on_screen()
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self._fade_in()

    def _position_on_screen(self) -> None:
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        if screen is None:
            self.move(100, 80)
            return
        geo = screen.availableGeometry()
        x = geo.x() + (geo.width() - self.width()) // 2
        y = geo.bottom() - self.height() - 36
        # Keep the banner fully on screen for unusual display setups.
        x = max(geo.x() + 8, min(x, geo.x() + geo.width() - self.width() - 8))
        y = max(geo.y() + 8, min(y, geo.bottom() - self.height() - 8))
        self.move(x, y)

    def _stop_animation(self) -> None:
        if self._fade_animation is not None:
            self._fade_animation.stop()
            self._fade_animation = None

    def _fade_in(self) -> None:
        self._stop_animation()
        self._fade_animation = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_animation.setDuration(180)
        self._fade_animation.setStartValue(0.0)
        self._fade_animation.setEndValue(1.0)
        self._fade_animation.start()

    def _fade_out(self) -> None:
        self._stop_animation()
        self._fade_animation = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_animation.setDuration(350)
        self._fade_animation.setStartValue(self.windowOpacity())
        self._fade_animation.setEndValue(0.0)
        self._fade_animation.finished.connect(self.hide)
        self._fade_animation.start()


class WorkerSignals(QObject):
    status: pyqtSignal = pyqtSignal(str) # pyright: ignore[reportAny]
    result: pyqtSignal = pyqtSignal(str, str, str, str, int) # pyright: ignore[reportAny]
    finished: pyqtSignal = pyqtSignal() # pyright: ignore[reportAny]
    error: pyqtSignal = pyqtSignal(str) # pyright: ignore[reportAny]

class SingleProofreadWorker(QThread):
    max_tokens: int
    settings: dict[str, Any]
    mode: str
    generic_target: dict[str, Any]
    signals: WorkerSignals

    def __init__(
        self,
        max_tokens: int,
        settings: dict[str, Any],
        mode: str = "word",
        generic_target: dict[str, Any] | None = None,
        activate_target: bool = True,
    ) -> None:
        super().__init__()
        self.max_tokens = max_tokens
        self.settings = settings
        self.mode = mode
        self.generic_target = generic_target or {}
        self.activate_target = activate_target
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            if self.mode == "generic":
                # No status is emitted before reading: the pill must never
                # appear before the selection is captured, or it can steal
                # focus from Mail/Outlook and break the copy. polish_selection_once
                # emits "Polishing N characters from X…" only after reading.
                status, original, corrected, comment, review_start = polish_selection_once(
                    self.max_tokens,
                    self.settings,
                    self.generic_target,
                    status_callback=self.signals.status.emit,
                    activate_target=self.activate_target,
                )
            else:
                provider_name = self.settings.get("active_provider", "")
                provider_info = PROVIDERS.get(provider_name, {})
                if provider_info.get("is_local"):
                    self.signals.status.emit("Preparing local AI…")
                else:
                    self.signals.status.emit("Proofreading…")
                status, original, corrected, comment, review_start = proofread_selection_once(
                    self.max_tokens,
                    self.settings,
                )
            self.signals.result.emit(
                status,
                original if original else "",
                corrected if corrected else "",
                comment if comment else "",
                review_start if review_start else 0,
            )
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.signals.finished.emit()

    @staticmethod
    def _is_self_target(target: dict[str, Any]) -> bool:
        """True when the frontmost app is ByteProof itself (e.g., button click)."""
        if target.get("pid") and target.get("pid") == os.getpid():
            return True
        name = str(target.get("name", "")).lower()
        bundle = str(target.get("bundle_id", "")).lower()
        exe = str(target.get("exe", "")).lower()
        return "byteproof" in name or "bytemind" in bundle or "byteproof" in exe


class UpdateCheckWorker(QThread):
    found = pyqtSignal(bool, object)  # pyright: ignore[reportAny]

    def __init__(self, current_version: str) -> None:
        super().__init__()
        self.current_version = current_version

    def run(self) -> None:
        try:
            update_available, version_info = check_for_updates(self.current_version)
            self.found.emit(bool(update_available), version_info)
        except Exception:
            self.found.emit(False, None)


class UpdateDownloadWorker(QThread):
    finished_download = pyqtSignal(str)  # pyright: ignore[reportAny]
    progress = pyqtSignal(int, int)  # pyright: ignore[reportAny]

    def __init__(self, version_info: dict[str, Any], downloads_dir: str) -> None:
        super().__init__()
        self.version_info = version_info
        self.downloads_dir = downloads_dir

    def run(self) -> None:
        try:
            path = download_update(
                self.version_info,
                self.downloads_dir,
                progress_callback=lambda done, total: self.progress.emit(done, total),
            )
            self.finished_download.emit(path or "")
        except Exception:
            self.finished_download.emit("")


class ConnectionTestWorker(QThread):
    result = pyqtSignal(bool, str)  # pyright: ignore[reportAny]

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        provider_name: str,
    ) -> None:
        super().__init__()
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.provider_name = provider_name

    def run(self) -> None:
        ok, message = test_provider_connection(
            self.api_key,
            self.base_url,
            self.model,
            self.provider_name,
        )
        self.result.emit(ok, message)


class LocalModelDownloadWorker(QThread):
    progress = pyqtSignal(int, int, str)  # pyright: ignore[reportAny]
    done = pyqtSignal(str)  # pyright: ignore[reportAny]
    failed = pyqtSignal(str)  # pyright: ignore[reportAny]
    cancelled = pyqtSignal()  # pyright: ignore[reportAny]

    def __init__(self, model_id: str) -> None:
        super().__init__()
        self.model_id = model_id
        self.cancel_event = threading.Event()

    def run(self) -> None:
        try:
            ensure_local_model(
                self.model_id,
                progress_callback=lambda done, total, stage: self.progress.emit(
                    done, total, stage
                ),
                cancel_event=self.cancel_event,
            )
            self.done.emit(self.model_id)
        except DownloadCancelledError:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class LocalServerWorker(QThread):
    progress = pyqtSignal(int, int, str)  # pyright: ignore[reportAny]
    result = pyqtSignal(bool, str)  # pyright: ignore[reportAny]
    cancelled = pyqtSignal()  # pyright: ignore[reportAny]

    def __init__(self, model_id: str | None, action: str) -> None:
        super().__init__()
        self.model_id = model_id
        self.action = action
        self.cancel_event = threading.Event()

    def run(self) -> None:
        try:
            if self.action == "start":
                start_local_server(
                    self.model_id,
                    progress_callback=lambda done, total, stage: self.progress.emit(
                        done, total, stage
                    ),
                    cancel_event=self.cancel_event,
                )
                info = local_server_info()
                self.result.emit(
                    True,
                    f"Local model server is running at {info.get('base_url')}.",
                )
            else:
                stop_local_server()
                self.result.emit(True, "Local model server stopped.")
        except DownloadCancelledError:
            self.cancelled.emit()
        except Exception as exc:
            self.result.emit(False, str(exc))


class GenericApplyWorker(QThread):
    done = pyqtSignal(bool, str)  # pyright: ignore[reportAny]

    def __init__(
        self,
        original: str,
        corrected: str,
        target: dict[str, Any],
    ) -> None:
        super().__init__()
        self.original = original
        self.corrected = corrected
        self.target = target

    def run(self) -> None:
        app_name = self.target.get("name") or "the app"
        try:
            editor = get_generic_editor()
            editor.activate(self.target)
            time.sleep(0.25)
            # Verify with at most one keystroke so a failed copy cannot produce
            # repeated system beeps.
            current = editor.get_selection_light(self.target)
            if normalize_selection_text(current) != normalize_selection_text(self.original):
                self.done.emit(
                    False,
                    "The selection changed while proofreading, so ByteProof did "
                    "not apply the text.",
                )
                return

            ok, message = editor.replace_selection(self.target, self.corrected)
            if not ok:
                self.done.emit(False, message or "Could not apply the text.")
                return

            # Verify the paste read-only (no keystrokes), so the system never
            # plays error beeps after editing.
            time.sleep(0.4)
            after = editor.get_selection_ax_only(self.target)
            ok_verify, _ = evaluate_apply_verification(
                self.original,
                self.corrected,
                after,
            )
            if ok_verify:
                self.done.emit(True, f"Applied to {app_name}.")
            else:
                self.done.emit(
                    False,
                    f"Could not apply to {app_name}. The original selection is "
                    "still in place — please try again.",
                )
        except Exception as e:
            self.done.emit(False, str(e))


class ActivationWorker(QThread):
    done = pyqtSignal(bool, str)  # pyright: ignore[reportAny]

    def __init__(self, kind: str, value: str) -> None:
        super().__init__()
        self.kind = kind
        self.value = value

    def run(self) -> None:
        try:
            if self.kind == "url":
                result = activate_from_url(self.value)
            elif self.kind == "email":
                result = activate_with_email(self.value)
            elif self.kind == "deactivate":
                result = deactivate_license()
            else:
                result = {"ok": False, "error": "Unknown activation type."}
            if result.get("ok"):
                self.done.emit(True, result.get("email") or "License activated.")
            else:
                self.done.emit(False, result.get("error") or "Activation failed.")
        except Exception as e:
            self.done.emit(False, str(e))


class LicenseValidationWorker(QThread):
    done = pyqtSignal(dict)  # pyright: ignore[reportAny]

    def run(self) -> None:
        try:
            result = validate_license_remote()
        except Exception as e:
            result = {"ok": False, "error": str(e)}
        self.done.emit(result)


class SettingsDialog(QDialog):
    settings: dict[str, Any]
    sidebar: QListWidget
    pages: QStackedWidget
    button_box: QDialogButtonBox
    chk_launch_login: QCheckBox
    chk_keep_top: QCheckBox
    chk_auto_apply: QCheckBox
    chk_sound: QCheckBox
    open_hotkey_edit: QKeySequenceEdit
    proofread_hotkey_edit: QKeySequenceEdit
    temp_label: QLabel
    temp_slider: QSlider
    combo_spelling: QComboBox
    combo_style: QComboBox
    combo_comment: QComboBox
    combo_context: QComboBox
    provider_buttons: dict[str, QPushButton]
    provider_status_labels: dict[str, QLabel]
    connect_page: QWidget | None
    local_page: QWidget | None
    local_model_cards: dict[str, dict[str, Any]]
    local_progress: QProgressBar
    local_status_label: QLabel
    local_engine_status: QLabel
    status_frame: QFrame
    lbl_status: QLabel
    lbl_msg: QLabel
    btn_buy: QPushButton
    btn_auto_activate: QPushButton

    def __init__(self, settings: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} Settings")
        screen = QApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.resize(
                min(760, max(560, geo.width() - 80)),
                min(560, max(420, geo.height() - 100)),
            )
        else:
            self.resize(720, 520)
        self.setMinimumSize(520, 360)
        self.setSizeGripEnabled(True)
        
        self.settings = copy.deepcopy(settings)
        
        self.provider_buttons = {}
        self.provider_status_labels = {}
        self.connect_page = None
        self.local_page = None
        self.local_model_cards = {}
        self.local_progress = QProgressBar()
        self.local_status_label = QLabel()
        self.local_engine_status = QLabel()
        self.combo_comment = QComboBox()
        self.combo_context = QComboBox()
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.sidebar = QListWidget()
        self.sidebar.setFixedWidth(190)
        self.sidebar.setObjectName("SettingsSidebar")
        self.sidebar.addItems(["General", "Connect", "Local AI", "License"])
        self.sidebar.currentRowChanged.connect(self.change_page)
        main_layout.addWidget(self.sidebar)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setStyleSheet("background-color: #E8E4E0; border: none; max-width: 1px;")
        divider.setFixedWidth(1)
        main_layout.addWidget(divider)

        content_container = QWidget()
        content_layout = QVBoxLayout(content_container)
        content_layout.setContentsMargins(24, 24, 24, 24)
        
        self.pages = QStackedWidget()
        content_layout.addWidget(self.pages)
        
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        content_layout.addWidget(self.button_box)
        
        main_layout.addWidget(content_container)

        self.init_general_tab()
        self.init_connect_tab()
        self.init_local_tab()
        self.init_license_tab()
        
        self.setStyleSheet("""
            QDialog { background-color: #FAF8F5; }
        """)
        self.sidebar.setStyleSheet("""
            QListWidget {
                background-color: #F5F0EB;
                border: none;
                font-size: 13px;
                padding: 14px 0;
                outline: 0;
            }
            QListWidget::item {
                height: 44px;
                padding-left: 20px;
                padding-right: 14px;
                margin: 2px 10px;
                color: #57534E;
                border-radius: 10px;
                font-weight: 520;
            }
            QListWidget::item:selected {
                background-color: #EDF3EF;
                color: #143024;
                font-weight: 620;
            }
            QListWidget::item:hover:!selected {
                background-color: #EDE6DF;
            }
        """)
        self.sidebar.setCurrentRow(0)

    def change_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)

    def init_general_tab(self) -> None:
        page = QWidget()
        outer_layout = QVBoxLayout(page)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(content)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setSpacing(18)
        
        title = QLabel("General")
        title.setObjectName("SettingsTitle")
        layout.addWidget(title)
        
        prefs_group = QGroupBox("Preferences")
        prefs_layout = QVBoxLayout(prefs_group)
        prefs_layout.setSpacing(12)
        
        self.chk_launch_login = QCheckBox("Launch at login")
        self.chk_launch_login.setChecked(self.settings.get("general", {}).get("launch_at_login", False))
        prefs_layout.addWidget(self.chk_launch_login)
        
        self.chk_keep_top = QCheckBox("Keep window on top")
        self.chk_keep_top.setChecked(self.settings.get("general", {}).get("keep_on_top", True))
        self.chk_keep_top.setToolTip("Tip: use the Quit button or the tray icon menu to close the app.")
        prefs_layout.addWidget(self.chk_keep_top)

        self.chk_auto_apply = QCheckBox("Auto-apply corrections to Word document")
        self.chk_auto_apply.setChecked(self.settings.get("general", {}).get("auto_apply", True))
        self.chk_auto_apply.setToolTip("When enabled, proofreading changes are applied directly to the Word document. When disabled, suggestions appear as comments instead.")
        prefs_layout.addWidget(self.chk_auto_apply)

        self.chk_sound = QCheckBox("Play sound when proofreading starts")
        self.chk_sound.setChecked(self.settings.get("general", {}).get("play_sound_on_proofread", True))
        self.chk_sound.setToolTip("Play a short chime when a proofreading task starts.")
        prefs_layout.addWidget(self.chk_sound)

        layout.addWidget(prefs_group)

        hotkey_group = QGroupBox("Hotkeys")
        hotkey_layout = QFormLayout(hotkey_group)
        hotkey_layout.setSpacing(12)
        hotkey_layout.setContentsMargins(12, 18, 12, 12)
        
        self.open_hotkey_edit = QKeySequenceEdit()
        try:
            self.open_hotkey_edit.setClearButtonEnabled(True)
        except AttributeError:
            pass
        open_seq_str = self.pynput_to_qt(self.settings.get("general", {}).get("open_hotkey", "<cmd>+<shift>+;"))
        self.open_hotkey_edit.setKeySequence(QKeySequence(open_seq_str))
        hotkey_layout.addRow("Open Window:", self.open_hotkey_edit)
        
        self.proofread_hotkey_edit = QKeySequenceEdit()
        try:
            self.proofread_hotkey_edit.setClearButtonEnabled(True)
        except AttributeError:
            pass
        proofread_seq_str = self.pynput_to_qt(self.settings.get("general", {}).get("proofread_hotkey", "<cmd>+<shift>+'"))
        self.proofread_hotkey_edit.setKeySequence(QKeySequence(proofread_seq_str))
        hotkey_layout.addRow("Proofread Selection:", self.proofread_hotkey_edit)
        
        layout.addWidget(hotkey_group)
        
        temp_group = QGroupBox("Proofreading Style (Temperature)")
        temp_layout = QVBoxLayout(temp_group)
        temp_layout.setSpacing(12)
        
        slider_grid = QGridLayout()
        slider_grid.setContentsMargins(0, 5, 0, 0)
        slider_grid.setVerticalSpacing(2)
        
        lbl_precise = QLabel("Precise")
        lbl_precise.setStyleSheet("color: #78716C; font-size: 12px; font-weight: 520;")
        lbl_precise.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        
        lbl_creative = QLabel("Creative")
        lbl_creative.setStyleSheet("color: #78716C; font-size: 12px; font-weight: 520;")
        lbl_creative.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        
        self.temp_label = QLabel()
        self.temp_label.setFixedWidth(40)
        self.temp_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.temp_label.setStyleSheet("font-size: 14px; font-weight: 680; color: #1A3A2A;")
        
        self.temp_slider = QSlider(Qt.Orientation.Horizontal)
        self.temp_slider.setRange(0, 20)
        self.temp_slider.setTickInterval(2)
        self.temp_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.temp_slider.setCursor(Qt.CursorShape.PointingHandCursor)
        
        current_temp = self.settings.get("general", {}).get("temperature", 0.3)
        current_temp = max(0.0, min(2.0, current_temp))
        self.temp_slider.setValue(int(current_temp * 10))
        
        slider_grid.addWidget(lbl_precise, 0, 0)
        slider_grid.addWidget(self.temp_slider, 0, 1)
        slider_grid.addWidget(lbl_creative, 0, 2)
        slider_grid.addWidget(self.temp_label, 0, 3)
        
        slider_grid.setColumnStretch(1, 1)
        
        temp_layout.addLayout(slider_grid)
        
        self.temp_slider.valueChanged.connect(self.update_temp_label)
        self.update_temp_label(self.temp_slider.value())
        
        layout.addWidget(temp_group)
        
        spelling_group = QGroupBox("Proofreading Settings")
        spelling_layout = QFormLayout(spelling_group)
        spelling_layout.setSpacing(12)
        spelling_layout.setContentsMargins(12, 18, 12, 12)
        
        self.combo_spelling = QComboBox()
        self.combo_spelling.addItems(["UK/AU/NZ", "US English"])
        
        current_spelling = self.settings.get("general", {}).get("spelling", "UK/AU/NZ")
        index = self.combo_spelling.findText(current_spelling)
        if index >= 0:
            self.combo_spelling.setCurrentIndex(index)
        else:
            self.combo_spelling.setCurrentIndex(0)
            
        spelling_layout.addRow("Preferred Spelling:", self.combo_spelling)
        
        self.combo_style = QComboBox()
        self.combo_style.addItems(["Precise (Minimal Changes)", "Creative (Rewrite)"])
        
        current_style = self.settings.get("general", {}).get("style", "Precise (Minimal Changes)")
        style_index = self.combo_style.findText(current_style)
        if style_index >= 0:
            self.combo_style.setCurrentIndex(style_index)
        else:
            self.combo_style.setCurrentIndex(0)
            
        spelling_layout.addRow("Editing Style:", self.combo_style)

        self.combo_comment = QComboBox()
        self.combo_comment.addItems(["None", "Language", "Technical (Reviewer)"])

        access = get_access_status()
        if access.get("tier") == "free":
            self.combo_comment.setCurrentIndex(0)
            self.combo_comment.setEnabled(False)
            self.combo_comment.setToolTip(
                "Reviewer comments require a ByteProof license."
            )

        if access.get("tier") != "free":
            current_comment = self.settings.get("general", {}).get("comment_type", "None")
            comment_index = self.combo_comment.findText(current_comment)
            if comment_index >= 0:
                self.combo_comment.setCurrentIndex(comment_index)
            else:
                self.combo_comment.setCurrentIndex(0)

        spelling_layout.addRow("Add Reviewer Comment:", self.combo_comment)

        self.combo_context = QComboBox()
        self.combo_context.addItems(["General Editing", "PhD Thesis Chapter", "Academic Journal (Top-Tier)"])

        current_context = self.settings.get("general", {}).get("context", "General Editing")
        context_index = self.combo_context.findText(current_context)
        if context_index >= 0:
            self.combo_context.setCurrentIndex(context_index)
        else:
            self.combo_context.setCurrentIndex(0)

        spelling_layout.addRow("Document Context:", self.combo_context)
        layout.addWidget(spelling_group)

        scroll.setWidget(content)
        outer_layout.addWidget(scroll)
        self.pages.addWidget(page)

    def update_temp_label(self, value: int) -> None:
        temp = value / 10.0
        self.temp_label.setText(f"{temp:.1f}")

    def pynput_to_qt(self, pynput_str: str) -> str:
        if not pynput_str:
            return ""
        parts = pynput_str.split('+')
        qt_parts = []
        is_macos = platform.system() == "Darwin"
        for p in parts:
            if p == '<cmd>': qt_parts.append('Meta' if is_macos else 'Ctrl')
            elif p == '<ctrl>': qt_parts.append('Ctrl')
            elif p == '<shift>': qt_parts.append('Shift')
            elif p == '<alt>': qt_parts.append('Alt')
            else: qt_parts.append(p.upper() if len(p)==1 else p.capitalize())
        return '+'.join(qt_parts)

    def qt_to_pynput(self, qt_str: str) -> str:
        if not qt_str:
            return ""
        parts = qt_str.split('+')
        pynput_parts = []
        is_macos = platform.system() == "Darwin"
        for p in parts:
            if p == 'Meta': pynput_parts.append('<cmd>' if is_macos else '<ctrl>')
            elif p == 'Ctrl': pynput_parts.append('<ctrl>')
            elif p == 'Shift': pynput_parts.append('<shift>')
            elif p == 'Alt': pynput_parts.append('<alt>')
            else: pynput_parts.append(p.lower())
        return '+'.join(pynput_parts)

    @staticmethod
    def display_hotkey(hk: str) -> str:
        if platform.system() == "Darwin":
            return (hk.replace('<cmd>', 'Cmd')
                      .replace('<shift>', 'Shift')
                      .replace('<ctrl>', 'Ctrl')
                      .replace('<alt>', 'Option'))
        return (hk.replace('<cmd>', 'Ctrl')
                  .replace('<shift>', 'Shift')
                  .replace('<ctrl>', 'Ctrl')
                  .replace('<alt>', 'Alt'))

    def init_connect_tab(self) -> None:
        page = QWidget()
        if self.connect_page is not None:
            self.pages.removeWidget(self.connect_page)
            self.connect_page.deleteLater()
        self.connect_page = page
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        title = QLabel("Connect")
        title.setObjectName("SettingsTitle")
        layout.addWidget(title)
        
        subtitle = QLabel("Select an AI provider. Free options are marked with a green badge.")
        subtitle.setStyleSheet("color: #78716C; font-size: 12px; margin-bottom: 4px;")
        layout.addWidget(subtitle)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        content_layout = QVBoxLayout(content)
        content_layout.setSpacing(8)
        content_layout.setContentsMargins(0, 0, 0, 0)
        
        active_provider = self.settings.get("active_provider", LOCAL_MODEL_PROVIDER)
        
        self.provider_buttons = {}
        self.provider_status_labels = {}
        
        for provider_name, provider_info in PROVIDERS.items():
            frame = QFrame()
            frame.setObjectName("ProviderCard")
            frame.setStyleSheet("""
                #ProviderCard {
                    background-color: rgba(255, 255, 255, 250);
                    border: 1px solid #E8E4E0;
                    border-radius: 12px;
                }
                #ProviderCard:hover {
                    border-color: #D6D0CA;
                }
            """)
            row = QHBoxLayout(frame)
            row.setContentsMargins(14, 12, 14, 12)
            
            info_layout = QVBoxLayout()
            info_layout.setSpacing(2)
            
            name_layout = QHBoxLayout()
            name_layout.setSpacing(8)
            
            lbl_name = QLabel(provider_name)
            lbl_name.setFont(_ui_font(13, QFont.Weight.Medium))
            name_layout.addWidget(lbl_name)
            
            if provider_info.get("is_local"):
                badge_text = "LOCAL"
                badge_style = (
                    "background-color: #DCFCE7; color: #166534; "
                    "font-size: 9px; font-weight: 700; padding: 2px 6px; "
                    "border-radius: 4px;"
                )
            elif provider_info.get("is_free"):
                badge_text = "FREE"
                badge_style = (
                    "background-color: #DCFCE7; color: #166534; "
                    "font-size: 9px; font-weight: 700; padding: 2px 6px; "
                    "border-radius: 4px;"
                )
            else:
                badge_text = ""
                badge_style = ""
            if badge_text:
                free_badge = QLabel(badge_text)
                free_badge.setStyleSheet(
                    badge_style
                )
                free_badge.setFixedHeight(18)
                name_layout.addWidget(free_badge)
            
            name_layout.addStretch()
            info_layout.addLayout(name_layout)
            
            keys = self.settings.get("providers", {}).get(provider_name, {}).get("api_keys", [])
            has_keys = len(keys) > 0
            is_ollama = provider_name == "Ollama (Local)"
            
            if provider_info.get("is_local"):
                status_text = "Private, offline · no API key needed"
                status_color = "#059669"
            elif is_ollama:
                status_text = "Runs locally — no API key needed"
                status_color = "#059669"
            elif has_keys:
                status_text = "API key configured"
                status_color = "#059669"
            else:
                status_text = "No API key set"
                status_color = "#A89F9A"
            
            if provider_name == active_provider:
                status_text += " · Active"
            
            lbl_status = QLabel(status_text)
            lbl_status.setStyleSheet(f"color: {status_color}; font-size: 11px;")
            self.provider_status_labels[provider_name] = lbl_status

            info_layout.addWidget(lbl_status)

            row.addLayout(info_layout, stretch=1)
            
            btn_activate = QPushButton("Use")
            btn_activate.setFixedWidth(56)
            btn_activate.setCheckable(True)
            btn_activate.setChecked(provider_name == active_provider)
            btn_activate.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_activate.clicked.connect(lambda checked, n=provider_name: self.set_active_provider(n))
            self.provider_buttons[provider_name] = btn_activate

            btn_set = QPushButton("Configure")
            btn_set.setFixedWidth(80)
            btn_set.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_set.clicked.connect(lambda checked, n=provider_name: self.open_provider_settings(n))
            
            row.addWidget(btn_activate)
            row.addWidget(btn_set)
            
            content_layout.addWidget(frame)
        
        scroll.setWidget(content)
        layout.addWidget(scroll)
        self.pages.addWidget(self.connect_page)
        self._refresh_connect_tab()

    def _refresh_connect_tab(self) -> None:
        """Update provider cards in place without rebuilding the page."""
        active_provider = self.settings.get("active_provider", LOCAL_MODEL_PROVIDER)
        access = get_access_status()
        free_mode = access.get("tier") == "free"
        for provider_name, btn in self.provider_buttons.items():
            btn.setChecked(provider_name == active_provider)
            provider_info = PROVIDERS.get(provider_name, {})
            is_local = bool(
                provider_info.get("is_local") or provider_name == "Ollama (Local)"
            )
            if free_mode and not is_local:
                btn.setEnabled(False)
                btn.setToolTip("Requires a ByteProof license")
            else:
                btn.setEnabled(True)
                btn.setToolTip("")
            if provider_name == active_provider:
                btn.setText("Active")
                btn.setStyleSheet("QPushButton { background-color: #1A3A2A; color: white; border: 1px solid #143024; border-radius: 10px; padding: 6px 12px; font-weight: 620; }")
            else:
                btn.setText("Use")
                btn.setStyleSheet("")

        for provider_name, lbl in self.provider_status_labels.items():
            keys = self.settings.get("providers", {}).get(provider_name, {}).get("api_keys", [])
            has_keys = len(keys) > 0
            is_ollama = provider_name == "Ollama (Local)"
            provider_info = PROVIDERS.get(provider_name, {})

            if provider_info.get("is_local"):
                status_text = "Private, offline · no API key needed"
                status_color = "#059669"
            elif is_ollama:
                status_text = "Runs locally — no API key needed"
                status_color = "#059669"
            elif has_keys:
                status_text = "API key configured"
                status_color = "#059669"
            else:
                status_text = "No API key set"
                status_color = "#A89F9A"

            if provider_name == active_provider:
                status_text += " · Active"
            if free_mode and not (
                provider_info.get("is_local") or provider_name == "Ollama (Local)"
            ):
                status_text += " · License required"
            lbl.setText(status_text)
            lbl.setStyleSheet(f"color: {status_color}; font-size: 11px;")

    def init_local_tab(self) -> None:
        page = QWidget()
        if self.local_page is not None:
            self.pages.removeWidget(self.local_page)
            self.local_page.deleteLater()
        self.local_page = page

        outer_layout = QVBoxLayout(page)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(content)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setSpacing(16)

        title = QLabel("Local AI")
        title.setObjectName("SettingsTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            "Private, offline proofreading on your computer. Download once and "
            "run it locally — no account, no API key, no internet needed. "
            "The $20 license unlocks unlimited use."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #78716C; font-size: 12px;")
        layout.addWidget(subtitle)

        hw = detect_hardware()
        recommended = recommend_model(hw)
        hw_card = QFrame()
        hw_card.setObjectName("ProviderCard")
        hw_card.setStyleSheet(
            "#ProviderCard { background-color: rgba(255,255,255,250); "
            "border: 1px solid #E8E4E0; border-radius: 12px; }"
        )
        hw_layout = QVBoxLayout(hw_card)
        hw_layout.setContentsMargins(14, 12, 14, 12)
        hw_layout.setSpacing(4)
        hw_title = QLabel("Your computer")
        hw_title.setFont(_ui_font(12, QFont.Weight.Bold))
        hw_layout.addWidget(hw_title)
        hw_line1 = QLabel(f"{hw['display_ram']} RAM · {hw['chip']}")
        hw_line1.setStyleSheet("color: #57534E; font-size: 12px;")
        hw_layout.addWidget(hw_line1)
        self.local_recommend_label = QLabel()
        self.local_recommend_label.setStyleSheet("color: #1F5335; font-size: 12px; font-weight: 600;")
        self.local_recommend_label.setText(
            f"Recommended: {recommended['name']} ({recommended['params']}) · "
            f"{model_size_label(recommended)}"
        )
        hw_layout.addWidget(self.local_recommend_label)
        layout.addWidget(hw_card)

        self.local_progress = QProgressBar()
        self.local_progress.setVisible(False)
        self.local_progress.setTextVisible(True)
        self.local_progress.setStyleSheet(
            "QProgressBar { border: 1px solid #A9C7B3; border-radius: 8px; "
            "background-color: #EDF3EF; height: 22px; text-align: center; "
            "font-size: 11px; color: #143024; }"
            "QProgressBar::chunk { background-color: #1F5335; border-radius: 7px; }"
        )
        layout.addWidget(self.local_progress)

        self.local_status_label = QLabel("")
        self.local_status_label.setWordWrap(True)
        self.local_status_label.setStyleSheet("color: #57534E; font-size: 12px;")
        layout.addWidget(self.local_status_label)

        models_title = QLabel("Models")
        models_title.setObjectName("SectionLabel")
        layout.addWidget(models_title)

        self.local_model_cards = {}
        for model in MODEL_CATALOG:
            card = QFrame()
            card.setObjectName("ProviderCard")
            card.setStyleSheet(
                "#ProviderCard { background-color: rgba(255,255,255,250); "
                "border: 1px solid #E8E4E0; border-radius: 12px; }"
            )
            row = QHBoxLayout(card)
            row.setContentsMargins(14, 12, 14, 12)
            row.setSpacing(10)

            info_col = QVBoxLayout()
            info_col.setSpacing(3)
            name_row = QHBoxLayout()
            name_row.setSpacing(8)
            name_lbl = QLabel(model["name"])
            name_lbl.setFont(_ui_font(13, QFont.Weight.Medium))
            name_row.addWidget(name_lbl)
            tag_lbl = QLabel(model["tag"])
            tag_lbl.setStyleSheet(
                "background-color: #EDF3EF; color: #1F5335; font-size: 9px; "
                "font-weight: 700; padding: 2px 6px; border-radius: 4px;"
            )
            tag_lbl.setFixedHeight(18)
            name_row.addWidget(tag_lbl)
            name_row.addStretch()
            info_col.addLayout(name_row)

            meta_lbl = QLabel(
                f"{model_size_label(model)} · needs {model['min_ram_gb']}+ GB RAM · "
                f"License: {model['license']}"
            )
            meta_lbl.setStyleSheet("color: #A89F9A; font-size: 11px;")
            info_col.addWidget(meta_lbl)

            status_lbl = QLabel("")
            status_lbl.setStyleSheet("color: #059669; font-size: 11px;")
            info_col.addWidget(status_lbl)

            desc_lbl = QLabel(model.get("description", ""))
            desc_lbl.setWordWrap(True)
            desc_lbl.setStyleSheet("color: #57534E; font-size: 11px;")
            info_col.addWidget(desc_lbl)

            row.addLayout(info_col, stretch=1)

            btn_use = QPushButton("Use")
            btn_use.setFixedWidth(56)
            btn_use.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn_use.clicked.connect(lambda checked=False, mid=model["id"]: self._use_local_model(mid))

            btn_download = QPushButton("Download")
            btn_download.setFixedWidth(96)
            btn_download.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn_download.clicked.connect(lambda checked=False, mid=model["id"]: self._download_local_model(mid))

            btn_remove = QPushButton("Remove")
            btn_remove.setFixedWidth(72)
            btn_remove.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn_remove.clicked.connect(lambda checked=False, mid=model["id"]: self._remove_local_model(mid))

            row.addWidget(btn_use)
            row.addWidget(btn_download)
            row.addWidget(btn_remove)
            layout.addWidget(card)

            self.local_model_cards[model["id"]] = {
                "status": status_lbl,
                "download": btn_download,
                "remove": btn_remove,
                "use": btn_use,
            }

        engine_card = QFrame()
        engine_card.setObjectName("ProviderCard")
        engine_card.setStyleSheet(
            "#ProviderCard { background-color: rgba(255,255,255,250); "
            "border: 1px solid #E8E4E0; border-radius: 12px; }"
        )
        engine_layout = QVBoxLayout(engine_card)
        engine_layout.setContentsMargins(14, 12, 14, 12)
        engine_layout.setSpacing(6)
        engine_title = QLabel("Local engine")
        engine_title.setFont(_ui_font(12, QFont.Weight.Bold))
        engine_layout.addWidget(engine_title)
        self.local_engine_status = QLabel()
        self.local_engine_status.setWordWrap(True)
        self.local_engine_status.setStyleSheet("color: #57534E; font-size: 12px;")
        engine_layout.addWidget(self.local_engine_status)
        engine_buttons = QHBoxLayout()
        self.btn_start_server = QPushButton("Start Server")
        self.btn_start_server.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_start_server.clicked.connect(self._start_local_server)
        self.btn_stop_server = QPushButton("Stop Server")
        self.btn_stop_server.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_stop_server.clicked.connect(self._stop_local_server)
        engine_buttons.addWidget(self.btn_start_server)
        engine_buttons.addWidget(self.btn_stop_server)
        engine_buttons.addStretch()
        engine_layout.addLayout(engine_buttons)
        layout.addWidget(engine_card)

        layout.addStretch()
        scroll.setWidget(content)
        outer_layout.addWidget(scroll)
        self.pages.addWidget(self.local_page)
        self._refresh_local_tab()

    def _use_local_model(self, model_id: str) -> None:
        self.settings["active_provider"] = LOCAL_MODEL_PROVIDER
        self.settings["local_model"]["active_model"] = model_id
        self.settings["providers"][LOCAL_MODEL_PROVIDER]["model"] = model_id
        parent = _find_owner_window(self)
        if parent is not None:
            parent._apply_local_model_selection(model_id)
        self._refresh_connect_tab()
        self._refresh_local_tab()

    def _download_local_model(self, model_id: str) -> None:
        if self._download_in_progress():
            self.local_status_label.setText("A download is already in progress.")
            return
        self._local_downloading = model_id
        for mid, widgets in self.local_model_cards.items():
            widgets["download"].setEnabled(mid != model_id)
            if mid == model_id:
                widgets["download"].setText("Downloading…")
        self.local_status_label.setStyleSheet("color: #57534E; font-size: 12px;")
        self.local_status_label.setText(f"Preparing {get_model(model_id)['name']}…")

        worker = LocalModelDownloadWorker(model_id)
        parent_window = _find_owner_window(self)
        if parent_window is not None:
            parent_window._local_download_worker = worker
        worker.progress.connect(self._on_local_download_progress)
        worker.done.connect(self._on_local_download_done)
        worker.failed.connect(self._on_local_download_failed)
        worker.cancelled.connect(self._on_local_download_cancelled)
        worker.start()

    def _on_local_download_progress(self, done: int, total: int, stage: str) -> None:
        try:
            shown_done = 0
            if total > 0:
                shown_done = min(done, total)
                pct = int(shown_done * 100 / total) if total else 0
                size_text = f"{_format_bytes(shown_done)} / {_format_bytes(total)} ({pct}%)"
                self.local_progress.setRange(0, total)
                self.local_progress.setValue(shown_done)
                self.local_progress.setFormat(size_text)
                self.local_status_label.setStyleSheet("color: #B45309; font-size: 12px;")
                self.local_status_label.setText(
                    f"{stage or 'Downloading'} — {size_text}"
                )
            else:
                self.local_progress.setRange(0, 0)
                self.local_progress.setFormat(stage or "Working…")
            self.local_progress.setVisible(True)
            downloading = getattr(self, "_local_downloading", None)
            if downloading is not None and downloading in self.local_model_cards:
                card_status = self.local_model_cards[downloading]["status"]
                if total > 0:
                    pct = int(shown_done * 100 / total) if total else 0
                    if stage.startswith("Verifying"):
                        card_status.setText(f"Verifying… {pct}%")
                    else:
                        card_status.setText(
                            f"Downloading {pct}% · {_format_bytes(shown_done)} / "
                            f"{_format_bytes(total)}"
                        )
                else:
                    card_status.setText(stage or "Downloading…")
                card_status.setStyleSheet("color: #B45309; font-size: 11px;")
        except RuntimeError:
            pass

    def _on_local_download_done(self, model_id: str) -> None:
        try:
            self._local_downloading = None
            self.local_progress.setVisible(False)
            self._use_local_model(model_id)
            self.local_status_label.setStyleSheet("color: #059669; font-size: 12px;")
            self.local_status_label.setText(
                f"{get_model(model_id)['name']} is installed and ready. "
                "It starts automatically when you proofread."
            )
        except RuntimeError:
            pass

    def _on_local_download_failed(self, error: str) -> None:
        try:
            self._local_downloading = None
            self.local_progress.setVisible(False)
            self.local_status_label.setStyleSheet("color: #B91C1C; font-size: 12px;")
            self.local_status_label.setText(f"Download failed: {error}")
            self._refresh_local_tab()
        except RuntimeError:
            pass

    def _on_local_download_cancelled(self) -> None:
        try:
            self._local_downloading = None
            self.local_progress.setVisible(False)
            self.local_status_label.setStyleSheet("color: #78716C; font-size: 12px;")
            self.local_status_label.setText(
                "Download cancelled. You can resume it later from this tab."
            )
            self._refresh_local_tab()
        except RuntimeError:
            pass

    def _remove_local_model(self, model_id: str) -> None:
        model = get_model(model_id)
        answer = QMessageBox.question(
            self,
            "Remove Model",
            f"Remove {model['name']} from this computer?\n\n"
            "You can download it again later.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        info = local_server_info()
        if info.get("running") and info.get("model_id") == model_id:
            stop_local_server()
        if remove_model(model_id):
            self._refresh_local_tab()
        else:
            QMessageBox.warning(self, "Remove Model", "Could not remove the model file.")

    def _start_local_server(self) -> None:
        if self._download_in_progress():
            self.local_status_label.setText(
                "Wait for the model download to finish, then start the server."
            )
            return
        model_id = (
            resolve_model_id(self.settings.get("local_model", {}).get("active_model"))
        )
        worker = LocalServerWorker(model_id, "start")
        parent_window = _find_owner_window(self)
        if parent_window is not None:
            parent_window._local_server_worker = worker
        worker.progress.connect(self._on_local_download_progress)
        worker.result.connect(self._on_local_server_result)
        worker.cancelled.connect(self._on_local_server_cancelled)
        worker.start()

    def _stop_local_server(self) -> None:
        worker = LocalServerWorker(None, "stop")
        parent_window = _find_owner_window(self)
        if parent_window is not None:
            parent_window._local_server_worker = worker
        worker.result.connect(self._on_local_server_result)
        worker.start()

    def _on_local_server_result(self, ok: bool, message: str) -> None:
        try:
            self.local_progress.setVisible(False)
            if ok:
                self.local_status_label.setStyleSheet("color: #059669; font-size: 12px;")
            else:
                self.local_status_label.setStyleSheet("color: #B91C1C; font-size: 12px;")
            self.local_status_label.setText(message)
            self._refresh_local_tab()
        except RuntimeError:
            pass

    def _on_local_server_cancelled(self) -> None:
        try:
            self.local_progress.setVisible(False)
            self.local_status_label.setStyleSheet("color: #78716C; font-size: 12px;")
            self.local_status_label.setText("Local AI startup cancelled.")
            self._refresh_local_tab()
        except RuntimeError:
            pass

    def _refresh_local_tab(self) -> None:
        if not hasattr(self, "local_model_cards"):
            return
        active_model = resolve_model_id(self.settings.get("local_model", {}).get("active_model"))
        self.settings["local_model"]["active_model"] = active_model
        self.settings["providers"][LOCAL_MODEL_PROVIDER]["model"] = active_model

        for model_id, widgets in self.local_model_cards.items():
            installed = is_model_installed(model_id)
            status_lbl = widgets["status"]
            download_btn = widgets["download"]
            remove_btn = widgets["remove"]
            use_btn = widgets["use"]
            is_active = (
                installed
                and model_id == active_model
                and self.settings.get("active_provider") == LOCAL_MODEL_PROVIDER
            )
            downloading = getattr(self, "_local_downloading", None) == model_id
            if downloading:
                status_lbl.setText("Downloading…")
                status_lbl.setStyleSheet("color: #B45309; font-size: 11px;")
                download_btn.setText("Downloading…")
                download_btn.setEnabled(False)
            elif installed:
                status_lbl.setText("Installed")
                status_lbl.setStyleSheet("color: #059669; font-size: 11px;")
                download_btn.setText("Installed")
                download_btn.setEnabled(False)
                remove_btn.setEnabled(True)
            else:
                status_lbl.setText("Not downloaded")
                status_lbl.setStyleSheet("color: #A89F9A; font-size: 11px;")
                download_btn.setText("Download")
                download_btn.setEnabled(True)
                remove_btn.setEnabled(False)
            if is_active:
                use_btn.setText("Active")
                use_btn.setEnabled(False)
                use_btn.setStyleSheet(
                    "QPushButton { background-color: #1A3A2A; color: white; "
                    "border: 1px solid #143024; border-radius: 10px; "
                    "padding: 6px 12px; font-weight: 620; }"
                    "QPushButton:disabled { background-color: #1A3A2A; color: white; "
                    "border: 1px solid #143024; }"
                )
            else:
                use_btn.setText("Use")
                use_btn.setEnabled(installed)
                use_btn.setStyleSheet("")

        info = local_server_info()
        if info.get("running"):
            self.local_engine_status.setStyleSheet("color: #059669; font-size: 12px;")
            self.local_engine_status.setText(
                f"Running · {info.get('model_id')} · {info.get('base_url')}"
            )
            self.btn_start_server.setEnabled(False)
            self.btn_stop_server.setEnabled(True)
        else:
            self.local_engine_status.setStyleSheet("color: #A89F9A; font-size: 12px;")
            self.local_engine_status.setText("Engine stopped. It starts automatically when you proofread.")
            self.btn_start_server.setEnabled(True)
            self.btn_stop_server.setEnabled(False)

    def _download_in_progress(self) -> bool:
        if getattr(self, "_local_downloading", None):
            return True
        parent = _find_owner_window(self)
        if parent is None:
            return False
        worker = getattr(parent, "_local_download_worker", None)
        return worker is not None and worker.isRunning()

    def init_license_tab(self) -> None:
        page = QWidget()
        outer_layout = QVBoxLayout(page)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(content)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setSpacing(20)
        
        header_layout = QHBoxLayout()
        header_layout.setSpacing(15)
        header_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        
        logo_path = resource_path(os.path.join("logo", "logo.svg"))
        if os.path.exists(logo_path):
            try:
                logo_widget = QSvgWidget(logo_path)
                logo_widget.setFixedSize(60, 60)
                logo_widget.setCursor(Qt.CursorShape.PointingHandCursor)
                def on_logo_click(a0):
                    webbrowser.open(PRODUCT_URL)
                logo_widget.mousePressEvent = on_logo_click
                header_layout.addWidget(logo_widget)
            except Exception:
                pass
        
        company_info_layout = QVBoxLayout()
        company_info_layout.setSpacing(2)
        
        lbl_company = QLabel(COMPANY_NAME)
        lbl_company.setFont(_ui_font(16, QFont.Weight.Bold))
        lbl_company.setStyleSheet("color: #292524;")
        
        lbl_reg = QLabel("A New Zealand registered company")
        lbl_reg.setFont(_ui_font(12))
        lbl_reg.setStyleSheet("color: #78716C;")
        
        lbl_email = QLabel(f'<a href="mailto:{SUPPORT_EMAIL}" style="color: #1A3A2A; text-decoration: none;">Contact: {SUPPORT_EMAIL}</a>')
        lbl_email.setFont(_ui_font(12))
        lbl_email.setOpenExternalLinks(True)
        lbl_email.setCursor(Qt.CursorShape.PointingHandCursor)
        lbl_website = QLabel(f'<a href="{PRODUCT_URL}" style="color: #1A3A2A; text-decoration: none;">{PRODUCT_URL.replace("https://", "")}</a>')
        lbl_website.setFont(_ui_font(12))
        lbl_website.setOpenExternalLinks(True)
        lbl_website.setCursor(Qt.CursorShape.PointingHandCursor)
        
        company_info_layout.addWidget(lbl_company)
        company_info_layout.addWidget(lbl_reg)
        company_info_layout.addWidget(lbl_email)
        company_info_layout.addWidget(lbl_website)
        header_layout.addLayout(company_info_layout)
        header_layout.addStretch()
        
        layout.addLayout(header_layout)
        
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background-color: #E8E4E0; max-height: 1px;")
        layout.addWidget(line)

        title = QLabel("License Status")
        title.setObjectName("SettingsTitle")
        layout.addWidget(title)
        
        self.status_frame = QFrame()
        self.status_frame.setObjectName("LicenseCard")
        self.status_frame.setStyleSheet("""
            #LicenseCard {
                background-color: rgba(255, 255, 255, 250);
                border: 1px solid #E8E4E0;
                border-radius: 12px;
                padding: 16px;
            }
        """)
        vbox = QVBoxLayout(self.status_frame)
        vbox.setSpacing(6)
        
        self.lbl_status = QLabel()
        self.lbl_status.setFont(_ui_font(14, QFont.Weight.Bold))
        
        self.lbl_msg = QLabel()
        
        lic_info = get_license_info()
        lic_status = lic_info.get("status", "unlicensed")
        trial_ts = ensure_trial_started()
        trial = get_trial_status(trial_ts)

        if lic_status == "licensed":
            self.lbl_status.setText("Licensed")
            self.lbl_status.setStyleSheet("color: #065F46;")
            self.lbl_msg.setText(f"Licensed to {lic_info.get('email', 'Unknown')}.")
            self.lbl_msg.setStyleSheet("color: #065F46; font-size: 12px;")
        elif lic_status == "expired":
            self.lbl_status.setText("License Expired")
            self.lbl_status.setStyleSheet("color: #B91C1C;")
            self.lbl_msg.setText(f"License for {lic_info.get('email', 'Unknown')} has expired. Please renew.")
            self.lbl_msg.setStyleSheet("color: #B91C1C; font-size: 12px;")
        elif trial["in_trial"]:
            self.lbl_status.setText(f"Free Trial ({trial['days_left']} day{'s' if trial['days_left'] != 1 else ''} left)")
            self.lbl_status.setStyleSheet("color: #D97706;")
            self.lbl_msg.setText("Support development by purchasing a license.")
            self.lbl_msg.setStyleSheet("color: #78716C; font-size: 12px;")
        else:
            self.lbl_status.setText("Trial Expired")
            self.lbl_status.setStyleSheet("color: #B91C1C;")
            self.lbl_msg.setText("Your free trial has ended. Purchase a license to continue.")
            self.lbl_msg.setStyleSheet("color: #B91C1C; font-size: 12px;")
        
        vbox.addWidget(self.lbl_status)
        vbox.addWidget(self.lbl_msg)
        layout.addWidget(self.status_frame)
        
        self.btn_buy = QPushButton("Purchase License ($1 Test)")
        self.btn_buy.setMinimumHeight(42)
        self.btn_buy.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_buy.setStyleSheet("""
            QPushButton {
                background-color: #1A3A2A;
                color: white;
                border-radius: 10px;
                font-weight: 620;
                border: 1px solid #143024;
                padding: 10px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #143024;
            }
            QPushButton:pressed {
                background-color: #0E2419;
            }
        """)
        self.btn_buy.clicked.connect(lambda: open_purchase_url(self))
        layout.addWidget(self.btn_buy)
        
        self.btn_auto_activate = QPushButton("Already Paid? Activate with Email")
        self.btn_auto_activate.setFlat(True)
        self.btn_auto_activate.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_auto_activate.setStyleSheet("""
            QPushButton {
                color: #1A3A2A;
                text-align: left;
                padding-left: 0;
                border: none;
                font-size: 12px;
            }
            QPushButton:hover {
                text-decoration: underline;
            }
        """)
        self.btn_auto_activate.clicked.connect(self._auto_activate_from_email)
        layout.addWidget(self.btn_auto_activate)

        self.btn_deactivate = QPushButton("Deactivate This Computer")
        self.btn_deactivate.setFlat(True)
        self.btn_deactivate.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_deactivate.setStyleSheet("""
            QPushButton {
                color: #B91C1C;
                text-align: left;
                padding-left: 0;
                border: none;
                font-size: 12px;
            }
            QPushButton:hover {
                text-decoration: underline;
            }
        """)
        self.btn_deactivate.clicked.connect(self.deactivate_license_clicked)
        layout.addWidget(self.btn_deactivate)
        
        if lic_status == "licensed" and lic_info.get("expiry") is None:
            self.btn_buy.setVisible(False)
            self.btn_auto_activate.setVisible(False)
            self.btn_deactivate.setVisible(True)
        else:
            self.btn_deactivate.setVisible(False)

        layout.addStretch()
        
        lbl_copy = QLabel(f"Version {APP_VERSION} · Copyright 2026 ByteMind Ltd. All rights reserved.")
        lbl_copy.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_copy.setStyleSheet("color: #A89F9A; font-size: 11px; padding-top: 12px;")
        layout.addWidget(lbl_copy)

        scroll.setWidget(content)
        outer_layout.addWidget(scroll)
        self.pages.addWidget(page)

    def set_active_provider(self, name: str) -> None:
        self.settings["active_provider"] = name
        self._refresh_connect_tab()

    def open_provider_settings(self, provider_name: str) -> None:
        provider_info = PROVIDERS.get(provider_name, {})
        if provider_info.get("is_local"):
            self.sidebar.setCurrentRow(2)
            self.change_page(2)
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Configure {provider_name}")
        dialog.setModal(True)
        dialog.resize(440, 320)
        layout = QVBoxLayout(dialog)
        layout.setSpacing(12)
        
        install_guide = provider_info.get("install_guide", "")
        is_ollama = provider_name == "Ollama (Local)"
        key_inputs: list[QLineEdit] = []
        
        if install_guide:
            guide_frame = QFrame()
            guide_frame.setStyleSheet(
                "QFrame { background-color: #EDF3EF; border: 1px solid #A9C7B3; "
                "border-radius: 8px; padding: 10px; }"
            )
            guide_layout = QVBoxLayout(guide_frame)
            guide_title = QLabel("Getting Started")
            guide_title.setFont(_ui_font(12, QFont.Weight.Bold))
            guide_title.setStyleSheet("color: #1F5335;")
            guide_layout.addWidget(guide_title)
            guide_text = QLabel(install_guide)
            guide_text.setWordWrap(True)
            guide_text.setStyleSheet("color: #143024; font-size: 12px;")
            guide_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            guide_layout.addWidget(guide_text)
            layout.addWidget(guide_frame)
        
        form = QFormLayout()
        
        default_url = PROVIDERS[provider_name]["base_url"]
        current_url = self.settings["providers"][provider_name].get("base_url", default_url)
        input_url = QLineEdit(current_url)
        if not is_ollama:
            form.addRow("Base URL:", input_url)
        
        default_model = PROVIDERS[provider_name]["model"]
        current_model = self.settings["providers"][provider_name].get("model", default_model)
        input_model = QLineEdit(current_model)
        model_label = "Model (change if using a different one):" if is_ollama else "Model:"
        form.addRow(model_label, input_model)
        
        layout.addLayout(form)
        
        if is_ollama:
            ollama_note = QLabel(
                "Ollama must be running in the background. Start it from "
                "Applications or run 'ollama serve' in Terminal."
            )
            ollama_note.setWordWrap(True)
            ollama_note.setStyleSheet("color: #78716C; font-size: 11px;")
            layout.addWidget(ollama_note)
        else:
            layout.addWidget(QLabel("API Keys (Up to 5):"))
            
            current_keys = self.settings["providers"][provider_name].get("api_keys", [])
            while len(current_keys) < 5:
                current_keys.append("")
                
            for i in range(5):
                inp = QLineEdit(current_keys[i])
                inp.setPlaceholderText(f"API Key {i+1}")
                inp.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
                key_inputs.append(inp)
                layout.addWidget(inp)

        test_row = QHBoxLayout()
        test_btn = QPushButton("Test Connection")
        test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        test_btn.setStyleSheet(
            "QPushButton { background-color: #EDF3EF; color: #143024; "
            "border: 1px solid #A9C7B3; border-radius: 10px; padding: 8px 14px; "
            "font-weight: 600; font-size: 12px; }"
            "QPushButton:hover { background-color: #D6E4DB; }"
            "QPushButton:disabled { color: #A89F9A; background-color: #F5F0EB; border-color: #E8E4E0; }"
        )
        test_result_lbl = QLabel("")
        test_result_lbl.setWordWrap(True)
        test_result_lbl.setStyleSheet("color: #78716C; font-size: 11px;")
        test_row.addWidget(test_btn)
        test_row.addWidget(test_result_lbl, stretch=1)
        layout.addLayout(test_row)
        dialog_closed = {"value": False}

        def _on_dialog_closed(_result: int) -> None:
            dialog_closed["value"] = True

        dialog.finished.connect(_on_dialog_closed)

        def _update_test_enabled() -> None:
            if is_ollama:
                test_btn.setEnabled(True)
            else:
                test_btn.setEnabled(any(k.text().strip() for k in key_inputs))

        if not is_ollama:
            for inp in key_inputs:
                inp.textChanged.connect(lambda *_: _update_test_enabled())
        _update_test_enabled()

        def run_connection_test() -> None:
            test_btn.setEnabled(False)
            test_btn.setText("Testing…")
            test_result_lbl.setText("")
            test_result_lbl.setStyleSheet("color: #78716C; font-size: 11px;")

            raw_url = input_url.text().strip()
            while raw_url.endswith("/"):
                raw_url = raw_url[:-1]
            first_key = ""
            if not is_ollama:
                first_key = next((k.text().strip() for k in key_inputs if k.text().strip()), "")

            worker = ConnectionTestWorker(
                first_key,
                raw_url,
                input_model.text().strip(),
                provider_name,
            )
            # Keep the worker alive on the main window; a dialog-owned
            # reference would let Qt destroy the thread while it is running.
            parent_window = _find_owner_window(self)
            if parent_window is not None:
                parent_window._provider_test_worker = worker
            worker.result.connect(lambda ok, msg: _on_test_done(ok, msg))
            worker.start()

        def _on_test_done(ok: bool, message: str) -> None:
            if dialog_closed["value"]:
                return
            try:
                test_btn.setEnabled(True)
                test_btn.setText("Test Connection")
                if ok:
                    test_result_lbl.setStyleSheet("color: #059669; font-size: 11px; font-weight: 600;")
                else:
                    test_result_lbl.setStyleSheet("color: #B91C1C; font-size: 11px;")
                test_result_lbl.setText(message)
            except RuntimeError:
                # Dialog was destroyed while the test was running.
                pass

        test_btn.clicked.connect(run_connection_test)
            
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if not is_ollama:
                new_keys = [k.text().strip() for k in key_inputs if k.text().strip()]
                self.settings["providers"][provider_name]["api_keys"] = new_keys
                
                raw_url = input_url.text().strip()
                raw_url = raw_url.removesuffix("/")
                self.settings["providers"][provider_name]["base_url"] = raw_url
            
            self.settings["providers"][provider_name]["model"] = input_model.text().strip()
            self._refresh_connect_tab()

    def _auto_activate_from_email(self) -> None:
        from PyQt6.QtWidgets import QInputDialog

        email, ok = QInputDialog.getText(
            self,
            "Activate with Email",
            "Enter the email you used at checkout:",
        )
        if not ok or not email.strip():
            return
        self.btn_auto_activate.setEnabled(False)
        worker = ActivationWorker("email", email.strip())
        parent_window = _find_owner_window(self)
        if parent_window is not None:
            parent_window._activation_worker = worker
        worker.done.connect(self._on_auto_activation_done)
        worker.start()

    def _on_auto_activation_done(self, ok: bool, message: str) -> None:
        self.btn_auto_activate.setEnabled(True)
        if ok:
            QMessageBox.information(
                self,
                "Activation Successful",
                f"ByteProof is now licensed for {message}.",
            )
            self.settings["license"]["status"] = "licensed"
            self._refresh_license_tab()
            parent = self.parent()
            if isinstance(parent, ProofreaderApp):
                parent._update_proofread_button()
            save_runtime_settings(self.settings)
        else:
            QMessageBox.warning(self, "Activation Failed", message)
        
    def get_settings(self) -> dict[str, Any]:
        self.settings["general"]["launch_at_login"] = self.chk_launch_login.isChecked()
        self.settings["general"]["keep_on_top"] = self.chk_keep_top.isChecked()
        self.settings["general"]["auto_apply"] = self.chk_auto_apply.isChecked()
        self.settings["general"]["play_sound_on_proofread"] = self.chk_sound.isChecked()
        self.settings["general"]["temperature"] = self.temp_slider.value() / 10.0
        self.settings["general"]["spelling"] = self.combo_spelling.currentText()
        self.settings["general"]["style"] = self.combo_style.currentText()
        self.settings["general"]["comment_type"] = self.combo_comment.currentText()
        self.settings["general"]["context"] = self.combo_context.currentText()
        
        open_seq = self.open_hotkey_edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        self.settings["general"]["open_hotkey"] = self.qt_to_pynput(open_seq)
        
        proofread_seq = self.proofread_hotkey_edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        self.settings["general"]["proofread_hotkey"] = self.qt_to_pynput(proofread_seq)
        
        return self.settings

    def _refresh_license_tab(self) -> None:
        lic_info = get_license_info()
        lic_status = lic_info.get("status", "unlicensed")
        trial_ts = ensure_trial_started()
        trial = get_trial_status(trial_ts)

        if lic_status == "licensed":
            self.lbl_status.setText("Licensed")
            self.lbl_status.setStyleSheet("color: #065F46;")
            self.lbl_msg.setText(
                f"Licensed to {lic_info.get('email', 'Unknown')}. "
                "Works on up to 2 computers."
            )
            self.lbl_msg.setStyleSheet("color: #065F46; font-size: 12px;")
            if lic_info.get("expiry") is None:
                self.btn_buy.setVisible(False)
                self.btn_auto_activate.setVisible(False)
                self.btn_deactivate.setVisible(True)
            else:
                self.btn_buy.setVisible(True)
                self.btn_auto_activate.setVisible(False)
                self.btn_deactivate.setVisible(False)
        elif lic_status == "expired":
            self.lbl_status.setText("License Expired")
            self.lbl_status.setStyleSheet("color: #B91C1C;")
            self.lbl_msg.setText(f"License for {lic_info.get('email', 'Unknown')} has expired. Please renew.")
            self.lbl_msg.setStyleSheet("color: #B91C1C; font-size: 12px;")
            self.btn_buy.setVisible(True)
            self.btn_auto_activate.setVisible(True)
            self.btn_deactivate.setVisible(False)
        elif trial["in_trial"]:
            self.lbl_status.setText(f"Free Trial ({trial['days_left']} day{'s' if trial['days_left'] != 1 else ''} left)")
            self.lbl_status.setStyleSheet("color: #D97706;")
            self.lbl_msg.setText(
                "Everything included for 7 days. Buy a license, or activate "
                "with the email you used at checkout."
            )
            self.lbl_msg.setStyleSheet("color: #78716C; font-size: 12px;")
            self.btn_buy.setVisible(True)
            self.btn_auto_activate.setVisible(True)
            self.btn_deactivate.setVisible(False)
        else:
            self.lbl_status.setText("Trial Expired")
            self.lbl_status.setStyleSheet("color: #B91C1C;")
            self.lbl_msg.setText(
                "Your free trial has ended. Buy a license, or activate with "
                "the email you used at checkout."
            )
            self.lbl_msg.setStyleSheet("color: #B91C1C; font-size: 12px;")
            self.btn_buy.setVisible(True)
            self.btn_auto_activate.setVisible(True)
            self.btn_deactivate.setVisible(False)

    def deactivate_license_clicked(self) -> None:
        """Deactivate this computer and free a slot on the license."""
        confirm = QMessageBox.question(
            self,
            "Deactivate License?",
            "This deactivates ByteProof on this computer and frees a device "
            "slot on your license.\n\n"
            "You can reactivate later by clicking the activation link again.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self.btn_deactivate.setEnabled(False)
        worker = ActivationWorker("deactivate", "")
        self._deactivation_worker = worker
        worker.done.connect(self._on_deactivation_done)
        worker.start()

    def _on_deactivation_done(self, ok: bool, message: str) -> None:
        self.btn_deactivate.setEnabled(True)
        if ok:
            QMessageBox.information(
                self,
                "License Deactivated",
                "ByteProof has been deactivated on this computer. "
                "A device slot is now free.",
            )
            self._refresh_license_tab()
            parent = _find_owner_window(self)
            if isinstance(parent, ProofreaderApp):
                parent._update_proofread_button()
                parent._show_toast("License deactivated on this computer.", kind="success")
        else:
            QMessageBox.warning(self, "Deactivation Failed", message)


class ProofreaderApp(QMainWindow):
    request_proofread: pyqtSignal = pyqtSignal() # pyright: ignore[reportAny]
    request_show: pyqtSignal = pyqtSignal() # pyright: ignore[reportAny]

    def __init__(self, max_tokens: int, settings: dict[str, Any]) -> None:
        super().__init__()
        self.max_tokens = max_tokens
        self.settings = dict(settings)
        
        keep_on_top = self.settings.get("general", {}).get("keep_on_top", True)
        
        self.proofread_actions: list[QAction] = []
        
        self.request_proofread.connect(self.run_proofread_task)
        self.request_show.connect(self.show_and_raise)
        
        self.setWindowTitle(APP_NAME)
        self.setGeometry(120, 120, 760, 540)
        
        if keep_on_top:
            self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        self._configure_theme()
        self._setup_menu_bar()
        self._setup_system_tray()
        app_inst = QApplication.instance()
        if app_inst is not None:
            try:
                app_inst.aboutToQuit.disconnect(self._on_about_to_quit)
            except TypeError:
                pass
            app_inst.aboutToQuit.connect(self._on_about_to_quit)
        icon_path = resource_path(os.path.join("logo", "logo.png"))
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        # Initialize global hotkey listener
        self.hotkey_listener = None
        self.hotkey_manager = None
        self.hotkey_needs_permission = False
        self.hotkey_permission_warned = False
        self.hotkey_retry_tick = 0
        self._hotkey_stop_slot = None
        
        self.hotkey_timer = QTimer(self)
        self.hotkey_timer.timeout.connect(self._poll_hotkeys)
        self.hotkey_timer.start(500)
        
        QTimer.singleShot(800, lambda: self._setup_hotkeys(show_permission_message=True))
        
        # Header Area
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        
        title_layout = QVBoxLayout()
        title_layout.setSpacing(2)
        
        header = QLabel(APP_NAME)
        header.setObjectName("TitleLabel")
        title_layout.addWidget(header)
        
        subtitle = QLabel("Academic proofreading for Word · Writing polish for any app")
        subtitle.setObjectName("MetaLabel")
        title_layout.addWidget(subtitle)
        
        header_layout.addLayout(title_layout)
        header_layout.addStretch()
        
        self.settings_btn = QPushButton("Settings")
        self.settings_btn.setObjectName("SettingsBtn")
        self.settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_btn.clicked.connect(self.open_settings)
        header_layout.addWidget(self.settings_btn)
        
        layout.addLayout(header_layout)

        # Status Bar Area
        status_container = QFrame()
        status_container.setObjectName("StatusBar")
        status_layout = QHBoxLayout(status_container)
        status_layout.setContentsMargins(14, 10, 14, 10)
        
        self.status_dot = QLabel()
        self.status_dot.setFixedSize(8, 8)
        self.status_dot.setStyleSheet("background-color: #059669; border-radius: 4px;")
        status_layout.addWidget(self.status_dot)
        
        self.status_label = QLabel("Ready")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("font-weight: 500; color: #57534E; font-size: 12px;")
        status_layout.addWidget(self.status_label, stretch=1)
        
        layout.addWidget(status_container)

        diff_group = QFrame()
        diff_group.setObjectName("Card")
        diff_layout = QVBoxLayout(diff_group)
        diff_layout.setContentsMargins(20, 18, 20, 18)
        diff_layout.setSpacing(12)
        
        diff_header = QHBoxLayout()
        diff_header.setContentsMargins(0, 0, 0, 0)
        
        diff_label = QLabel("Proposed Changes")
        diff_label.setObjectName("SectionLabel")
        diff_header.addWidget(diff_label)
        diff_header.addStretch()

        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setVisible(False)
        self.apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_btn.setStyleSheet(
            "QPushButton { background-color: #1A3A2A; color: white; "
            "border: 1px solid #143024; border-radius: 8px; padding: 4px 14px; "
            "font-size: 11px; font-weight: 700; }"
            "QPushButton:hover { background-color: #143024; }"
            "QPushButton:disabled { background-color: #A9C7B3; color: #F5F0EB; border-color: #79A88A; }"
        )
        self.apply_btn.clicked.connect(self._apply_pending_generic)
        diff_header.addWidget(self.apply_btn)

        self.copy_btn = QPushButton("Copy")
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.setEnabled(False)
        self.copy_btn.setStyleSheet(
            "QPushButton { background-color: #EDF3EF; color: #143024; "
            "border: 1px solid #A9C7B3; border-radius: 8px; padding: 4px 12px; "
            "font-size: 11px; font-weight: 600; }"
            "QPushButton:hover { background-color: #D6E4DB; }"
            "QPushButton:disabled { color: #A89F9A; background-color: #F5F0EB; border-color: #E8E4E0; }"
        )
        self.copy_btn.clicked.connect(self._copy_corrected_text)
        diff_header.addWidget(self.copy_btn)
        
        self.diff_word_count = QLabel("")
        self.diff_word_count.setStyleSheet("color: #A89F9A; font-size: 11px; font-weight: 420;")
        diff_header.addWidget(self.diff_word_count)
        
        diff_layout.addLayout(diff_header)

        self.diff_text = QTextEdit()
        self.diff_text.setReadOnly(True)
        self.diff_text.setPlaceholderText("Select text in Word or any app, press the proofread hotkey, and the proposed changes will appear here.")
        self.diff_text.setFont(_mono_font(12))
        self.diff_text.setStyleSheet("QTextEdit { background-color: #FFFFFF; border: 1px solid #E8E4E0; border-radius: 12px; selection-background-color: #D6E4DB; selection-color: #431407; padding: 16px; line-height: 1.55; color: #44403C; } QTextEdit:focus { border-color: #D6D0CA; }")
        diff_layout.addWidget(self.diff_text, stretch=1)
        
        layout.addWidget(diff_group, stretch=1)

        controls_layout = QVBoxLayout()
        controls_layout.setSpacing(0)
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)
        
        self.run_btn = QPushButton("Proofread Selection Now")
        self.run_btn.setObjectName("ProofreadBtn")
        self.run_btn.setMinimumHeight(46)
        self.run_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.run_btn.clicked.connect(self.run_proofread_task)
        btn_layout.addWidget(self.run_btn, stretch=3)
        
        self.quit_btn = QPushButton("Quit")
        self.quit_btn.setObjectName("SecondaryBtn")
        self.quit_btn.setMinimumHeight(46)
        self.quit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.quit_btn.clicked.connect(QApplication.quit)
        btn_layout.addWidget(self.quit_btn, stretch=1)
        
        controls_layout.addLayout(btn_layout)
        
        self.hotkey_hint = QLabel("")
        self.hotkey_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hotkey_hint.setStyleSheet("color: #A89F9A; font-size: 11px; margin-top: 8px;")
        controls_layout.addWidget(self.hotkey_hint)
        
        layout.addLayout(controls_layout)

        self.worker = None
        self.last_corrected = ""
        self.pending_generic_apply: dict[str, Any] | None = None
        self.pending_generic_preview: dict[str, Any] | None = None
        self.generic_apply_worker: GenericApplyWorker | None = None
        self._generic_apply_pending = False
        self.last_generic_target: dict[str, Any] | None = None
        self._last_other_app: dict[str, Any] | None = None
        self._app_history: list[dict[str, Any]] = []
        self._last_escape_ts = 0.0
        self._active_settings_dialog: SettingsDialog | None = None
        self._hotkey_target: dict[str, Any] | None = None
        self._app_observer_token = None
        self._provider_test_worker: ConnectionTestWorker | None = None
        self._activation_worker: ActivationWorker | None = None
        self.toast = ToastNotification()
        self._start_app_tracking()
        register_url_scheme()

        app_inst = QApplication.instance()
        if app_inst is not None:
            app_inst.installEventFilter(self)
            self._app_event_filter_installed = True
            self.destroyed.connect(self._remove_app_event_filter)
        else:
            self._app_event_filter_installed = False
        
        self._update_proofread_button()
        QTimer.singleShot(500, self.check_api_keys)
        QTimer.singleShot(600, self._sync_launch_at_login)
        QTimer.singleShot(1200, self._check_trial_status_at_startup)
        QTimer.singleShot(1800, self._validate_license_at_startup)
        QTimer.singleShot(3000, self._check_for_app_updates)

    def _copy_corrected_text(self) -> None:
        if not self.last_corrected:
            return
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.last_corrected)
        self.status_label.setText("Corrected text copied to clipboard.")
        self.copy_btn.setText("Copied ✓")
        QTimer.singleShot(1400, lambda: self.copy_btn.setText("Copy"))

    def _sync_launch_at_login(self) -> None:
        """Re-apply launch-at-login on startup so a moved app keeps working."""
        if self.settings.get("general", {}).get("launch_at_login", False):
            set_launch_at_login(True)

    def _on_proofread_hotkey(self) -> None:
        """Capture the frontmost app at the exact moment of the hotkey press.

        Querying the frontmost app later (inside the task) can race with
        window activation; capturing it here, while the source app is still in
        front, makes the hotkey path reliable.
        """
        try:
            editor = get_generic_editor()
            target = editor.frontmost_app()
            if target and not SingleProofreadWorker._is_self_target(target):
                self._hotkey_target = target
        except Exception:
            pass
        self.request_proofread.emit()

    def _start_app_tracking(self) -> None:
        """Remember the last non-ByteProof app on macOS.

        Clicking the tray menu or the ByteProof button makes ByteProof the
        frontmost app, which hides the app the user actually came from. This
        observer records every other app as it becomes active so we can still
        proofread in it.
        """
        if platform.system() != "Darwin":
            return
        try:
            from AppKit import (
                NSWorkspace,
                NSWorkspaceDidActivateApplicationNotification,
            )
            center = NSWorkspace.sharedWorkspace().notificationCenter()
            token = center.addObserverForName_object_queue_usingBlock_(
                NSWorkspaceDidActivateApplicationNotification,
                None,
                None,
                lambda note: self._on_other_app_activated(note),
            )
            self._app_observer_token = token
        except Exception as e:
            print(f"App tracking unavailable: {e}")

    def _on_other_app_activated(self, notification: Any = None) -> None:
        try:
            from AppKit import NSWorkspace
            app = None
            if notification is not None:
                info = notification.userInfo()
                if info:
                    app = info.get("NSWorkspaceApplicationKey")
            if app is None:
                app = NSWorkspace.sharedWorkspace().frontmostApplication()
            if app is None:
                return
            bundle = app.bundleIdentifier() or ""
            name = app.localizedName() or ""
            if "bytemind" in bundle.lower() or "byteproof" in name.lower():
                return
            entry = {
                "pid": app.processIdentifier(),
                "name": name,
                "bundle_id": bundle,
            }
            history = [
                item for item in self._app_history if item.get("pid") != entry.get("pid")
            ]
            history.insert(0, entry)
            self._app_history = history[:6]
            self._last_other_app = entry
        except Exception:
            pass

    def _find_target_with_selection(self, editor: Any) -> dict[str, Any] | None:
        """Find the app that actually has text selected.

        ByteProof itself is frontmost when the user triggers via the button or
        tray, so the source app is not visible. We probe recent apps read-only
        (Accessibility only, no keystrokes) and only activate an app after it
        proves it has a selection. History is searched first so a stale
        selection in an unrelated background app is never preferred over the
        app the user actually came from.
        """
        history_candidates: list[dict[str, Any]] = []
        seen: set[int] = set()
        for candidate in (
            getattr(self, "_last_other_app", None),
            getattr(self, "last_generic_target", None),
            *getattr(self, "_app_history", []),
        ):
            if not candidate:
                continue
            pid = candidate.get("pid")
            if pid in seen:
                continue
            seen.add(pid)
            if editor.is_word(candidate) or SingleProofreadWorker._is_self_target(candidate):
                continue
            history_candidates.append(candidate)

        # Probe without switching apps.
        for candidate in history_candidates:
            try:
                text = editor.get_selection_ax_only(candidate)
            except Exception:
                text = ""
            if text and text.strip():
                return candidate

        # Activate candidates one at a time and re-read (clipboard fallback is
        # safe once the candidate is frontmost). Limit to history so we never
        # flicker through unrelated apps.
        for candidate in history_candidates[:5]:
            try:
                editor.activate(candidate)
                time.sleep(0.25)
            except Exception:
                continue
            try:
                text, _, _ = editor.get_selection_info(candidate)
            except Exception:
                text = ""
            if text and text.strip():
                return candidate

        # Last resort: scan every running app read-only (never activates them).
        # Covers the case where history is empty (e.g., right after a reboot).
        try:
            for app in editor.running_apps():
                pid = app.get("pid")
                if pid in seen:
                    continue
                seen.add(pid)
                if editor.is_word(app) or SingleProofreadWorker._is_self_target(app):
                    continue
                try:
                    text = editor.get_selection_ax_only(app)
                except Exception:
                    text = ""
                if text and text.strip():
                    return app
        except Exception:
            pass
        return None

    def _ask_user_for_target(self, editor: Any) -> dict[str, Any] | None:
        """Let the user pick the app when automatic detection finds nothing."""
        from PyQt6.QtWidgets import QInputDialog

        apps = editor.running_apps()
        if not apps:
            return None
        labels: list[str] = []
        for app in apps:
            name = app.get("name") or "Unknown"
            bundle = app.get("bundle_id") or ""
            labels.append(f"{name} ({bundle})" if bundle else name)
        choice, ok = QInputDialog.getItem(
            self,
            "Choose App",
            "Which app has your selected text?",
            labels,
            0,
            False,
        )
        if not ok:
            return None
        try:
            index = labels.index(choice)
        except ValueError:
            return None
        return apps[index]

    def _show_toast(self, message: str, kind: str = "success") -> None:
        if kind == "processing":
            self.toast.show_processing(message)
        else:
            self.toast.complete(message, kind=kind)

    def _cancel_proofread_start(self, message: str) -> None:
        """Undo the starting state when no reliable target can be found."""
        self.run_btn.setEnabled(True)
        self.status_dot.setStyleSheet("background-color: #B91C1C; border-radius: 4px;")
        self.status_label.setText(message)
        toast_message = message if len(message) <= 64 else message[:61] + "…"
        self._show_toast(toast_message, kind="warning")
        self.apply_btn.setVisible(False)
        if hasattr(self, "tray_icon") and hasattr(self, "normal_icon") and not self.normal_icon.isNull():
            self.tray_icon.setIcon(self.normal_icon)

    def _on_worker_status(self, message: str) -> None:
        self.status_label.setText(message)
        if not self.toast.is_processing():
            self._show_toast(message, kind="processing")
        else:
            self.toast.update_message(message)

    def _poll_hotkeys(self) -> None:
        if self.hotkey_needs_permission:
            self.hotkey_retry_tick += 1
            if self.hotkey_retry_tick >= 8:
                self.hotkey_retry_tick = 0
                self._setup_hotkeys(show_permission_message=False)

    def check_api_keys(self) -> None:
        active = self.settings.get("active_provider", LOCAL_MODEL_PROVIDER)
        provider_data = self.settings.get("providers", {}).get(active, {})
        provider_info = PROVIDERS.get(active, {})

        if provider_info.get("is_local"):
            if not any(
                is_model_installed(model["id"]) for model in MODEL_CATALOG
            ):
                msg = QMessageBox(self)
                msg.setWindowTitle(f"Welcome to {APP_NAME}")
                msg.setText("ByteProof's private local AI is ready to download.")
                msg.setInformativeText(
                    "Download a small local AI model to proofread offline with no "
                    "API key and no monthly fee. The right size is picked "
                    "automatically for your computer."
                )
                msg.setIcon(QMessageBox.Icon.Information)
                msg.setStandardButtons(QMessageBox.StandardButton.Ok)
                msg.exec()
                self.open_settings_local()
            return

        api_keys = provider_data.get("api_keys", [])
        
        if not api_keys and active != "Ollama (Local)":
            msg = QMessageBox(self)
            msg.setWindowTitle(f"Welcome to {APP_NAME}")
            msg.setText(f"Please configure API keys for {active}.")
            msg.setInformativeText(
                "You need to add at least one API key to use the proofreading features, "
                "or choose ByteProof Local in Settings."
            )
            msg.setIcon(QMessageBox.Icon.Information)
            msg.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg.exec()
            self.open_settings()

    def _setup_hotkeys(self, show_permission_message: bool = True) -> None:
        self.hotkey_timer.start(500)
        try:
            if self.hotkey_manager:
                self.hotkey_manager.stop()
                self.hotkey_manager = None
        except Exception as e:
            print(f"Error stopping hotkeys: {e}")

        open_hk = self.settings.get("general", {}).get("open_hotkey", "<cmd>+<shift>+;")
        proofread_hk = self.settings.get("general", {}).get("proofread_hotkey", "<cmd>+<shift>+'")
        
        if hasattr(self, 'hotkey_hint') and self.hotkey_hint is not None:
            open_display = SettingsDialog.display_hotkey(open_hk)
            proof_display = SettingsDialog.display_hotkey(proofread_hk)
            self.hotkey_hint.setText(f"Hotkeys: {proof_display} to proofread  ·  {open_display} to open window")
        
        hotkeys_dict = {}
        if open_hk:
            hotkeys_dict[open_hk] = lambda: self.request_show.emit()
        if proofread_hk:
            hotkeys_dict[proofread_hk] = lambda: self._on_proofread_hotkey()
            
        if not hotkeys_dict:
            print("No hotkeys defined.")
            self.hotkey_timer.stop()
            return
            
        try:
            from .hotkeys import HotkeyManager, log_debug
            log_debug(f"Attempting to start hotkeys with {hotkeys_dict}")
            self.hotkey_manager = HotkeyManager(hotkeys_dict)
            started = self.hotkey_manager.start(prompt_user=False)
            log_debug(f"HotkeyManager started={started}")
            self.hotkey_needs_permission = not started and not self.hotkey_manager.has_permission()
            if self.hotkey_needs_permission:
                if HotkeyManager.check_permission_silently():
                    self.hotkey_needs_permission = False
                    # Permission was granted between the failed start and this
                    # check; try once more so the user does not have to restart.
                    started = self.hotkey_manager.start(prompt_user=False)
                    self.status_label.setText("Ready" if started else "Hotkeys disabled — restart the app.")
                else:
                    self.status_label.setText("Hotkeys disabled — grant Accessibility permission.")
                    if show_permission_message and not self.hotkey_permission_warned:
                        self.hotkey_permission_warned = True
                        self._show_hotkey_permission_message()
            else:
                self.status_label.setText("Ready")
                self.hotkey_retry_tick = 0
                self.hotkey_timer.stop()
            app_inst = QApplication.instance()
            if app_inst is not None:
                if self._hotkey_stop_slot is not None:
                    try:
                        app_inst.aboutToQuit.disconnect(self._hotkey_stop_slot)  # pyright: ignore[reportAny]
                    except TypeError:
                        pass
                    self._hotkey_stop_slot = None
                self._hotkey_stop_slot = self.hotkey_manager.stop
                app_inst.aboutToQuit.connect(self._hotkey_stop_slot)  # pyright: ignore[reportAny]
        except Exception as e:
            print(f"Error starting global hotkey manager: {e}")

    def _show_hotkey_permission_message(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Accessibility Permission Required")
        dlg.setModal(True)
        dlg.setFixedSize(460, 260)
        dlg.setStyleSheet("""
            QDialog { background-color: #FAF8F5; }
            QLabel#PermTitle { font-size: 15px; font-weight: 700; color: #292524; }
            QLabel#PermDesc { font-size: 13px; color: #57534E; line-height: 1.5; }
            QLabel#PermTip { font-size: 11px; color: #78716C; }
            QPushButton { border-radius: 8px; padding: 10px 22px; font-size: 13px; font-weight: 600; min-width: 130px; }
            QPushButton#PermOpenBtn { background-color: #1A3A2A; color: white; border: none; }
            QPushButton#PermOpenBtn:hover { background-color: #143024; }
            QPushButton#PermCancelBtn { background-color: #E8E4E0; color: #57534E; border: 1px solid #D6D0CA; }
            QPushButton#PermCancelBtn:hover { background-color: #D6D0CA; }
        """)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 20, 24, 20)

        title = QLabel("Global Hotkey Access Needed")
        title.setObjectName("PermTitle")
        layout.addWidget(title)

        desc = QLabel(
            "ByteProof requires Accessibility permission to register "
            "global keyboard shortcuts. Without this, hotkeys will not work."
        )
        desc.setObjectName("PermDesc")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        tip = QLabel(
            "If ByteProof is already listed but hotkeys still do not work, "
            "remove it with the minus (−) button, reopen the app, and re-enable it."
        )
        tip.setObjectName("PermTip")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        layout.addStretch()

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        cancel_btn = QPushButton("Not Now")
        cancel_btn.setObjectName("PermCancelBtn")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)

        open_btn = QPushButton("Open Accessibility Settings")
        open_btn.setObjectName("PermOpenBtn")
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)

        btn_layout.addWidget(cancel_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(open_btn)
        layout.addLayout(btn_layout)

        cancel_btn.clicked.connect(lambda: dlg.done(0))
        open_btn.clicked.connect(lambda: dlg.done(1))

        result = dlg.exec()
        if result == 1:
            if platform.system() == "Darwin":
                webbrowser.open("x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")

    def _setup_system_tray(self) -> None:
        self.tray_icon = QSystemTrayIcon(self)
        icon_path = resource_path(os.path.join("logo", "logo.png"))
        
        self.normal_icon = QIcon()
        self.active_icon = QIcon()
        
        if os.path.exists(icon_path):
            from PyQt6.QtCore import Qt
            from PyQt6.QtGui import QColor, QPainter, QPixmap
            
            self.normal_icon = QIcon(icon_path)
            
            # Create active icon with a green dot
            pixmap = QPixmap(icon_path)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(QColor("#306D49"))
            painter.setPen(Qt.PenStyle.NoPen)
            size = pixmap.size()
            radius = size.width() // 4
            painter.drawEllipse(size.width() - radius - 2, size.height() - radius - 2, radius, radius)
            painter.end()
            self.active_icon = QIcon(pixmap)
            
            self.tray_icon.setIcon(self.normal_icon)

        tray_menu = QMenu()
        
        show_action = QAction("Show Window", self)
        show_action.triggered.connect(self.show_and_raise)
        tray_menu.addAction(show_action)

        proofread_action = QAction("Proofread Selection", self)
        proofread_action.triggered.connect(self.run_proofread_task)
        tray_menu.addAction(proofread_action)
        self.proofread_actions.append(proofread_action)

        tray_menu.addSeparator()

        settings_action = QAction("Settings...", self)
        settings_action.triggered.connect(self.open_settings)
        tray_menu.addAction(settings_action)

        check_updates_action = QAction("Check for Updates…", self)
        check_updates_action.triggered.connect(lambda: self._check_for_app_updates(force=True))
        tray_menu.addAction(check_updates_action)

        tray_menu.addSeparator()
        diagnostics_action = QAction("Copy Capture Diagnostics", self)
        diagnostics_action.triggered.connect(self._copy_capture_diagnostics)
        tray_menu.addAction(diagnostics_action)

        open_log_action = QAction("Open Log Folder", self)
        open_log_action.triggered.connect(self._open_support_folder)
        tray_menu.addAction(open_log_action)
        
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(QApplication.quit)
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.setToolTip(APP_NAME)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_and_raise()

    def eventFilter(self, a0: Any, a1: Any) -> bool:  # pyright: ignore[reportAny]
        # Clicking the Dock icon (or otherwise switching to ByteProof) brings
        # the hidden main window back.
        if a1.type() == QEvent.Type.ApplicationActivate:
            if self.isHidden():
                self.show()
                self.raise_()
                self.activateWindow()
        elif a1.type() == QEvent.Type.FileOpen:
            url = a1.url().toString()
            if url.startswith("byteproof://"):
                QTimer.singleShot(0, lambda: self._start_activation("url", url))
        elif a1.type() == QEvent.Type.KeyPress and a1.key() == Qt.Key.Key_Escape:
            if self._has_active_local_task():
                now = time.monotonic()
                if now - self._last_escape_ts < 0.7:
                    self._last_escape_ts = 0.0
                    self._cancel_active_local_task()
                    self.status_label.setText("Cancelling download…")
                    dlg = self._active_settings_dialog
                    if dlg is not None and hasattr(dlg, "local_status_label"):
                        dlg.local_status_label.setText("Cancelling download…")
                    return True
                self._last_escape_ts = now
                self.status_label.setText("Press Esc again to cancel.")
                dlg = self._active_settings_dialog
                if dlg is not None and hasattr(dlg, "local_status_label"):
                    dlg.local_status_label.setText("Press Esc again to cancel.")
                return True
        return super().eventFilter(a0, a1)

    def _has_active_local_task(self) -> bool:
        for attr in ("_local_download_worker", "_local_server_worker"):
            worker = getattr(self, attr, None)
            if worker is not None and worker.isRunning():
                return True
        return False

    def _cancel_active_local_task(self) -> None:
        for attr in ("_local_download_worker", "_local_server_worker"):
            worker = getattr(self, attr, None)
            if worker is not None and worker.isRunning():
                worker.cancel_event.set()

    def _on_about_to_quit(self) -> None:
        """Cancel in-flight downloads/server starts before the app exits."""
        self._cancel_active_local_task()
        for attr in ("_local_download_worker", "_local_server_worker"):
            worker = getattr(self, attr, None)
            if worker is not None and worker.isRunning():
                # The download loop checks cancellation between chunks; one
                # stalled socket read can take up to the 60s timeout.
                worker.wait(60_000)
        stop_local_server()

    def _check_trial_status_at_startup(self) -> None:
        if is_licensed():
            return
        trial = get_trial_status(ensure_trial_started())
        if trial["in_trial"] and 0 < trial["days_left"] <= 3:
            if trial["days_left"] == 1:
                message = "Trial ends tomorrow — purchase a license to keep unlimited access."
            else:
                message = (
                    f"Trial ends in {trial['days_left']} days — "
                    "purchase a license to keep unlimited access."
                )
            self._show_toast(
                message,
                kind="warning",
            )

    def _validate_license_at_startup(self) -> None:
        """Best-effort server validation, mirroring VoiceInk's validate flow."""
        if not is_licensed():
            return
        worker = LicenseValidationWorker()
        self._license_validation_worker = worker
        worker.done.connect(self._on_license_validation_result)
        worker.start()

    def _on_license_validation_result(self, result: dict) -> None:
        if result.get("valid") is False:
            self._show_toast(
                "Your license could not be verified online. If you deactivated "
                "this computer or changed hardware, click your activation email "
                "link again.",
                kind="warning",
            )

    def _start_activation(self, kind: str, value: str) -> None:
        worker = ActivationWorker(kind, value)
        self._activation_worker = worker
        worker.done.connect(self._on_activation_done)
        worker.start()

    def _on_activation_done(self, ok: bool, message: str) -> None:
        if ok:
            self._update_proofread_button()
            self._show_toast(f"License activated for {message}.", kind="success")
            QMessageBox.information(
                self,
                "Activation Successful",
                f"ByteProof is now licensed for {message}.",
            )
        else:
            self._show_toast(message, kind="error")
            QMessageBox.warning(self, "Activation Failed", message)

    def _prompt_auto_activation(self) -> None:
        from PyQt6.QtWidgets import QInputDialog

        email, ok = QInputDialog.getText(
            self,
            "Activate with Email",
            "Enter the email you used at checkout:",
        )
        if ok and email.strip():
            self._start_activation("email", email.strip())

    def _remove_app_event_filter(self) -> None:
        app_inst = QApplication.instance()
        if app_inst is not None and self._app_event_filter_installed:
            app_inst.removeEventFilter(self)
            self._app_event_filter_installed = False

    def _setup_menu_bar(self) -> None:
        menu_bar = self.menuBar()
        if menu_bar is None:
            return
        menu = menu_bar.addMenu("Application")
        if menu is None:
            return
        proofread_action = QAction("Proofread Selection", self)
        proofread_action.triggered.connect(self.run_proofread_task)
        menu.addAction(proofread_action)
        self.proofread_actions.append(proofread_action)
        
        preferences_action = QAction("Settings…", self)
        preferences_action.setMenuRole(QAction.MenuRole.PreferencesRole)
        prefs_shortcut = "Meta+," if platform.system() == "Darwin" else "Ctrl+,"
        preferences_action.setShortcut(prefs_shortcut)
        preferences_action.triggered.connect(self.open_settings)
        menu.addAction(preferences_action)

        check_updates_action = QAction("Check for Updates…", self)
        check_updates_action.triggered.connect(lambda: self._check_for_app_updates(force=True))
        menu.addAction(check_updates_action)

        diagnostics_action = QAction("Copy Capture Diagnostics", self)
        diagnostics_action.triggered.connect(self._copy_capture_diagnostics)
        menu.addAction(diagnostics_action)

        open_log_action = QAction("Open Log Folder", self)
        open_log_action.triggered.connect(self._open_support_folder)
        menu.addAction(open_log_action)
        menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.setMenuRole(QAction.MenuRole.QuitRole)
        quit_shortcut = "Meta+Q" if platform.system() == "Darwin" else "Ctrl+Q"
        quit_action.setShortcut(quit_shortcut)
        quit_action.triggered.connect(QApplication.quit)
        menu.addAction(quit_action)

    def _configure_theme(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow {
                background-color: #FAF8F5;
            }
            QWidget {
                color: #292524;
                font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                font-size: 13px;
            }
            #SettingsBtn {
                background-color: rgba(255, 255, 255, 220);
                border: 1px solid #E8E4E0;
                border-radius: 10px;
                color: #57534E;
                font-weight: 520;
                font-size: 12px;
                padding: 8px 16px;
                margin-top: 4px;
            }
            #SettingsBtn:hover {
                background-color: #FFFFFF;
                color: #292524;
                border-color: #D6D0CA;
            }
            #SettingsBtn:pressed {
                background-color: #F5F0EB;
                border-color: #C4BDB7;
            }
            #TitleLabel {
                font-size: 26px;
                font-weight: 680;
                color: #292524;
                letter-spacing: -0.4px;
                margin-bottom: 0px;
            }
            #MetaLabel {
                color: #78716C;
                font-size: 12px;
                font-weight: 420;
                margin-bottom: 4px;
            }
            #SectionLabel {
                font-size: 13px;
                font-weight: 600;
                color: #292524;
                margin-top: 4px;
                letter-spacing: -0.1px;
            }
            #SettingsTitle {
                font-size: 20px;
                font-weight: 680;
                color: #292524;
                margin-bottom: 14px;
                letter-spacing: -0.3px;
            }
            #StatusBar {
                background-color: rgba(255, 255, 255, 230);
                border: 1px solid #E8E4E0;
                border-radius: 12px;
                margin-bottom: 8px;
            }
            #Card, #ProviderCard, #LicenseCard {
                background-color: rgba(255, 255, 255, 250);
                border: 1px solid #E8E4E0;
                border-radius: 14px;
            }
            QTextEdit {
                background-color: #FFFFFF;
                border: 1px solid #E8E4E0;
                border-radius: 12px;
                selection-background-color: #D6E4DB;
                selection-color: #431407;
                padding: 16px;
                line-height: 1.55;
                color: #44403C;
            }
            QTextEdit:focus {
                border-color: #D6D0CA;
            }
            QLineEdit {
                background-color: #FFFFFF;
                border: 1px solid #E8E4E0;
                border-radius: 10px;
                padding: 10px 12px;
                selection-background-color: #D6E4DB;
                selection-color: #431407;
                color: #292524;
            }
            QLineEdit:focus {
                border-color: #1A3A2A;
            }
            QComboBox {
                background-color: #FFFFFF;
                border: 1px solid #E8E4E0;
                border-radius: 10px;
                padding: 9px 12px;
                min-height: 22px;
                color: #292524;
            }
            QComboBox:hover {
                border-color: #C4BDB7;
            }
            QComboBox:focus {
                border-color: #1A3A2A;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 30px;
                border-left: 1px solid #F0ECE8;
                border-top-right-radius: 10px;
                border-bottom-right-radius: 10px;
            }
            QComboBox QAbstractItemView {
                background-color: #FFFFFF;
                border: 1px solid #E8E4E0;
                border-radius: 10px;
                selection-background-color: #EDF3EF;
                selection-color: #292524;
                padding: 6px;
                outline: none;
            }
            QPushButton {
                background-color: #FFFFFF;
                border: 1px solid #E8E4E0;
                border-radius: 10px;
                color: #292524;
                padding: 10px 20px;
                font-weight: 520;
                font-size: 13px;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #FAF8F5;
                border-color: #C4BDB7;
            }
            QPushButton:pressed {
                background-color: #F5F0EB;
                border-color: #A89F9A;
            }
            QPushButton:disabled {
                background-color: #FAF8F5;
                color: #C4BDB7;
                border-color: #F0ECE8;
            }
            QPushButton#ProofreadBtn {
                background-color: #1A3A2A;
                color: #FFFFFF;
                border: 1px solid #143024;
                font-weight: 620;
                font-size: 14px;
                letter-spacing: -0.2px;
            }
            QPushButton#ProofreadBtn:hover {
                background-color: #143024;
                border-color: #0E2419;
            }
            QPushButton#ProofreadBtn:pressed {
                background-color: #0E2419;
                border-color: #0E2419;
            }
            QPushButton#ProofreadBtn:disabled {
                background-color: #A9C7B3;
                color: rgba(255, 255, 255, 180);
                border-color: #79A88A;
            }
            QPushButton#SecondaryBtn {
                background-color: rgba(255, 255, 255, 200);
                color: #78716C;
                border: 1px solid #E8E4E0;
                font-weight: 420;
            }
            QPushButton#SecondaryBtn:hover {
                background-color: #FEF2F2;
                color: #B91C1C;
                border-color: #FECACA;
            }
            QPushButton#SecondaryBtn:pressed {
                background-color: #FEE2E2;
                border-color: #FCA5A5;
            }
            QListWidget {
                outline: 0;
                background-color: transparent;
            }
            QGroupBox {
                font-weight: 620;
                font-size: 12px;
                color: #57534E;
                border: 1px solid #E8E4E0;
                border-radius: 12px;
                margin-top: 18px;
                background-color: rgba(255, 255, 255, 150);
                padding-top: 30px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 14px;
                padding: 0 8px;
                background-color: transparent;
            }
            QSlider::groove:horizontal {
                border: none;
                height: 6px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #1A3A2A, stop:0.45 #D97706, stop:1 #F59E0B);
                margin: 2px 0;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #FFFFFF;
                border: 2px solid #D6D0CA;
                width: 20px;
                height: 20px;
                margin: -8px 0;
                border-radius: 10px;
            }
            QSlider::handle:horizontal:hover {
                border-color: #1A3A2A;
                background: #F2EFE5;
            }
            QSlider::sub-page:horizontal {
                background: transparent;
                border-radius: 3px;
            }
            QCheckBox {
                spacing: 10px;
                color: #44403C;
            }
            QCheckBox::indicator {
                width: 20px;
                height: 20px;
                border: 2px solid #D6D0CA;
                border-radius: 6px;
                background-color: #FFFFFF;
            }
            QCheckBox::indicator:checked {
                background-color: #1A3A2A;
                border-color: #1A3A2A;
            }
            QCheckBox::indicator:hover {
                border-color: #1A3A2A;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
                margin: 4px 2px;
            }
            QScrollBar::handle:vertical {
                background: #D6D0CA;
                border-radius: 4px;
                min-height: 32px;
            }
            QScrollBar::handle:vertical:hover {
                background: #A89F9A;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0;
            }
            """
        )

    def current_settings(self) -> dict[str, Any]:
        return self.settings

    def show_and_raise(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized | Qt.WindowState.WindowActive)

    def _show_purchase_dialog(self, title: str, text: str, recap: str = "") -> None:
        msg = QMessageBox(self)
        msg.setWindowTitle(title)
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setText(text)
        info = recap
        if info:
            info += "\n\n"
        info += (
            "Click 'Purchase' to buy a license. If you have already paid, "
            "choose 'Already Paid — Activate with Email' and enter the email "
            "you used at checkout."
        )
        msg.setInformativeText(info)
        buy_btn = msg.addButton("Purchase", QMessageBox.ButtonRole.ActionRole)
        paid_btn = msg.addButton(
            "Already Paid — Activate with Email",
            QMessageBox.ButtonRole.ActionRole,
        )
        msg.addButton(QMessageBox.StandardButton.Cancel)
        msg.exec()

        if msg.clickedButton() == buy_btn:
            open_purchase_url(self)
        elif msg.clickedButton() == paid_btn:
            self._prompt_auto_activation()

    def _check_license_access(self) -> bool:
        access = get_access_status()
        if access.get("licensed") or access.get("in_trial"):
            return True

        provider = self.settings.get("active_provider", LOCAL_MODEL_PROVIDER)
        provider_info = PROVIDERS.get(provider, {})
        is_local = bool(provider_info.get("is_local") or provider == "Ollama (Local)")

        trial_used = int(access.get("trial_usage", 0))
        recap = (
            f"You proofread {trial_used} selection"
            f"{'s' if trial_used != 1 else ''} during your trial."
        )

        if not access.get("free_mode_allowed"):
            self._show_purchase_dialog(
                "Free Limit Reached",
                f"You've used all {access.get('daily_limit', 3)} free proofreads for today.",
                recap,
            )
            return False

        if not is_local:
            self._show_purchase_dialog(
                "Cloud Providers Require a License",
                "Cloud AI providers are available with a ByteProof license.",
                recap,
            )
            return False

        return True

    def _update_proofread_button(self) -> None:
        access = get_access_status()
        if access.get("licensed"):
            self.run_btn.setText("Proofread Selection Now")
            self.run_btn.setToolTip("")
            self.run_btn.setEnabled(True)
            return

        if access.get("in_trial"):
            days = int(access.get("days_left", 0))
            self.run_btn.setText("Proofread Selection Now")
            self.run_btn.setToolTip(
                f"{days} day{'' if days == 1 else 's'} remaining in free trial"
            )
            self.run_btn.setEnabled(True)
            return

        remaining = max(
            0,
            int(access.get("daily_limit", 3)) - int(access.get("daily_count", 0)),
        )
        if remaining > 0:
            self.run_btn.setText(f"Free mode - {remaining} left today")
            self.run_btn.setToolTip(
                f"Local AI only · {remaining} proofread"
                f"{'' if remaining == 1 else 's'} left today · "
                "Purchase a license for unlimited use"
            )
            self.run_btn.setEnabled(True)
        else:
            self.run_btn.setText("Free Limit Reached - Upgrade")
            self.run_btn.setToolTip(
                "You've used all your free proofreads for today. "
                "Purchase a license to continue."
            )
            self.run_btn.setEnabled(True)

    def _proofread_consumes_usage(self, status_text: str) -> bool:
        if not status_text:
            return False
        if status_text.startswith("Error"):
            return False
        if status_text.startswith("REVIEW_NEEDED:"):
            return True
        non_consuming = (
            "Selection is empty.",
            "Selection too short.",
            "Skipped:",
            "No text selected",
            "No API keys configured",
            "No app with selected text",
            "Could not detect",
            "Free mode is limited",
            "You have used all your free proofreads",
        )
        return not status_text.startswith(non_consuming)

    def _record_usage_if_completed(self, status_text: str) -> None:
        if self._proofread_consumes_usage(status_text):
            record_proofread_usage()
            self._update_proofread_button()

    def _open_license_tab(self) -> None:
        try:
            dialog = SettingsDialog(self.current_settings(), self)
            self._active_settings_dialog = dialog
            dialog.finished.connect(
                lambda _result: setattr(self, "_active_settings_dialog", None)
            )
            dialog.sidebar.setCurrentRow(3)
            dialog.change_page(3)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.settings = dialog.get_settings()
                save_runtime_settings(self.settings)
            self._update_proofread_button()
        except Exception as e:
            print(f"Error opening license tab: {e}")

    def _apply_local_model_selection(self, model_id: str) -> None:
        """Persist a local model choice made from the Settings dialog."""
        self.settings["active_provider"] = LOCAL_MODEL_PROVIDER
        self.settings["local_model"]["active_model"] = model_id
        self.settings["providers"][LOCAL_MODEL_PROVIDER]["model"] = model_id
        save_runtime_settings(self.settings)

    def open_settings_local(self) -> None:
        try:
            dialog = SettingsDialog(self.current_settings(), self)
            self._active_settings_dialog = dialog
            dialog.finished.connect(
                lambda _result: setattr(self, "_active_settings_dialog", None)
            )
            dialog.sidebar.setCurrentRow(2)
            dialog.change_page(2)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.settings = dialog.get_settings()
                save_runtime_settings(self.settings)
            self._update_proofread_button()
        except Exception as e:
            print(f"Error opening local AI settings: {e}")

    def run_proofread_task(self) -> None:
        if not self._check_license_access():
            return

        if self.worker is not None and self.worker.isRunning():
            self.status_label.setText("A proofreading task is already in progress.")
            return

        self.run_btn.setEnabled(False)
        self.status_dot.setStyleSheet("background-color: #D97706; border-radius: 4px;")
        self.status_label.setText("Initializing...")
        self.diff_text.clear()
        self.diff_text.setPlaceholderText("")
        self.diff_word_count.setText("")
        self._set_corrected_for_copy("")
        self.pending_generic_apply = None
        self.pending_generic_preview = None
        self._generic_apply_pending = False
        self.apply_btn.setVisible(False)
        
        if hasattr(self, 'tray_icon') and hasattr(self, 'active_icon') and not self.active_icon.isNull():
            self.tray_icon.setIcon(self.active_icon)
        
        current_settings = self.current_settings()
        temp = current_settings.get("general", {}).get("temperature", 0.3)
        print(f"Starting proofread task. Temp: {temp}")

        # Detect the target app on the main thread. AppKit is not reliable from
        # a worker thread, which previously caused generic edits to fall back
        # to Word mode and show the "open a Word document" message.
        mode = "word"
        generic_target: dict[str, Any] = {}
        activate_target = False
        try:
            editor = get_generic_editor()
            target = getattr(self, "_hotkey_target", None)
            self._hotkey_target = None
            if not target:
                target = editor.frontmost_app()
            if target and editor.is_word(target):
                mode = "word"
            elif target and not SingleProofreadWorker._is_self_target(target):
                mode = "generic"
                generic_target = target
                # Hotkey path: the target was frontmost at the key press. If
                # it is somehow no longer frontmost, allow worker activation.
                try:
                    front = editor.frontmost_app()
                    activate_target = not (front and front.get("pid") == target.get("pid"))
                except Exception:
                    activate_target = True
            elif not target:
                self._cancel_proofread_start(
                    "Could not detect the active app. Switch to the app with "
                    "your selected text and use the hotkey."
                )
                return
            else:
                # ByteProof is in front (button/tray). Find the app that
                # actually has selected text instead of guessing from history.
                best = self._find_target_with_selection(editor)
                if not best:
                    best = self._ask_user_for_target(editor)
                if not best:
                    self._cancel_proofread_start(
                        "No app with selected text found. Switch to the app "
                        "with your selected text and use the hotkey."
                    )
                    return
                if editor.is_word(best):
                    mode = "word"
                else:
                    mode = "generic"
                    generic_target = best
                    try:
                        editor.activate(best)
                        time.sleep(0.5)
                        front = editor.frontmost_app()
                        if not front or front.get("pid") != best.get("pid"):
                            # Office apps can be slow to become key; give them
                            # a second chance before falling back.
                            time.sleep(0.5)
                            front = editor.frontmost_app()
                        activate_target = not (front and front.get("pid") == best.get("pid"))
                    except Exception:
                        activate_target = True
        except Exception as e:
            print(f"Target detection failed: {e}")
            self._cancel_proofread_start(
                "Could not detect the active app. Switch to the app with "
                "your selected text and use the hotkey."
            )
            return

        self.worker = SingleProofreadWorker(
            self.max_tokens,
            current_settings,
            mode=mode,
            generic_target=generic_target,
            activate_target=activate_target,
        )
        self.worker.signals.result.connect(self.handle_result)
        self.worker.signals.error.connect(self.handle_error)
        self.worker.signals.status.connect(self._on_worker_status)
        self.worker.signals.finished.connect(self.task_finished)
        self.worker.start()
        if self.settings.get("general", {}).get("play_sound_on_proofread", True):
            play_start_sound()

    def _check_for_app_updates(self, force: bool = False) -> None:
        self._update_check_force = force
        self.update_check_worker = UpdateCheckWorker(APP_VERSION)
        self.update_check_worker.found.connect(self._handle_update_check_result)
        self.update_check_worker.start()

    def _set_corrected_for_copy(self, text: str) -> None:
        self.last_corrected = text or ""
        self.copy_btn.setEnabled(bool(self.last_corrected))

    def _handle_update_check_result(
        self, update_available: bool, version_info: dict[str, Any] | None
    ) -> None:
        force = getattr(self, "_update_check_force", False)
        if not update_available or version_info is None:
            if force:
                self._show_toast(f"You're up to date (v{APP_VERSION}).", kind="success")
            return

        remote_version = version_info.get("version", "")
        release_date = version_info.get("release_date", "")
        release_notes = version_info.get("release_notes", "")
        if not force and is_update_dismissed(remote_version, self.settings):
            return
        self.pending_update_version = remote_version

        msg = QMessageBox(self)
        msg.setWindowTitle("Update Available")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText(f"{APP_NAME} {remote_version} is available (you have {APP_VERSION}).")
        info_text = f"Released: {release_date}"
        if release_notes:
            info_text += f"\n\n{release_notes}"
        msg.setInformativeText(info_text)
        download_btn = msg.addButton("Download and Install", QMessageBox.ButtonRole.AcceptRole)
        msg.addButton("Remind Me Later", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(download_btn)
        msg.exec()

        if msg.clickedButton() != download_btn:
            if remote_version:
                self.settings.setdefault("general", {})["skipped_update_version"] = remote_version
                save_runtime_settings(self.settings)
            return

        self._download_update(version_info)

    def _download_update(self, version_info: dict[str, Any]) -> None:
        self.status_label.setText("Downloading update...")

        if platform.system() == "Windows":
            downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")
        else:
            downloads_dir = os.path.expanduser("~/Downloads")

        self.update_download_worker = UpdateDownloadWorker(version_info, downloads_dir)
        self.update_download_worker.finished_download.connect(self._handle_download_finished)
        self.update_download_worker.progress.connect(self._on_update_progress)
        self.update_download_worker.start()

    def _on_update_progress(self, downloaded: int, total: int) -> None:
        if total > 0:
            percent = min(100, int(downloaded * 100 / total))
            self.status_label.setText(f"Downloading update… {percent}%")
        else:
            self.status_label.setText("Downloading update…")

    def _handle_download_finished(self, download_path: str) -> None:
        if download_path and os.path.exists(download_path):
            webbrowser.open("file://" + download_path)
            self.status_label.setText("Update downloaded. Opening installer...")
            done_msg = QMessageBox(self)
            done_msg.setWindowTitle("Download Complete")
            done_msg.setIcon(QMessageBox.Icon.Information)
            done_msg.setText(f"{APP_NAME} {self.pending_update_version} has been downloaded.")
            if platform.system() == "Windows":
                done_informative = (
                    "The installer has been opened.\n\n"
                    "If it did not start automatically, open the downloaded file in your Downloads folder.\n"
                    "Replace the existing ByteProof folder when asked to complete the update."
                )
            else:
                done_informative = (
                    "The installer has been opened.\n\n"
                    "Double-click the DMG file in your Downloads folder if it did not open automatically.\n"
                    "Drag ByteProof to Applications to complete the update."
                )
            done_msg.setInformativeText(
                done_informative
            )
            done_msg.exec()
        else:
            self.status_label.setText("Ready")
            err_msg = QMessageBox(self)
            err_msg.setWindowTitle("Download Failed")
            err_msg.setIcon(QMessageBox.Icon.Warning)
            err_msg.setText("Could not download the update.")
            err_msg.setInformativeText(
                f"You can download it manually from the {APP_NAME} website.\n\n"
                f"{PRODUCT_URL}"
            )
            open_btn = err_msg.addButton("Open Website", QMessageBox.ButtonRole.AcceptRole)
            err_msg.addButton(QMessageBox.StandardButton.Ok)
            err_msg.exec()
            if err_msg.clickedButton() == open_btn:
                webbrowser.open(PRODUCT_URL)
        self.status_label.setText("Ready")

    def closeEvent(self, a0: Any) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._on_about_to_quit()
            QApplication.quit()
            if a0 is not None:
                a0.accept()
            return
        self.hide()
        if a0 is not None:
            a0.ignore()

    def handle_result(self, status_text: str, original: str, corrected: str, comment: str = "", review_start: int = 0) -> None:
        self._record_usage_if_completed(status_text)
        self._set_corrected_for_copy(corrected)
        if getattr(self.worker, "mode", "word") == "generic":
            self._handle_generic_result(status_text, original, corrected, comment)
            return
        if status_text == TABLE_SKIPPED_STATUS:
            QMessageBox.warning(
                self,
                "Table Detected",
                "The selected text contains a table.\n\nPlease select text excluding tables to proceed with proofreading.",
                QMessageBox.StandardButton.Ok
            )
            self.status_label.setText("Proofreading skipped (Table detected).")
            self.diff_text.clear()
            self.diff_word_count.setText("")
            return

        if status_text.startswith("Error: Microsoft Word is not running"):
            self._show_word_unavailable(status_text, word_running=False)
            return
        if status_text.startswith("Error: No active Word document"):
            self._show_word_unavailable(status_text, word_running=True)
            return

        if status_text.startswith("REVIEW_NEEDED:"):
            sim_pct = status_text.split(":", 1)[1]
            try:
                sim_float = float(sim_pct)
            except ValueError:
                sim_float = 0.0

            dlg = QDialog(self)
            dlg.setWindowTitle("Unusual Result — Review Needed")
            dlg.setModal(True)
            dlg.setFixedSize(480, 280)
            dlg.setStyleSheet("""
                QDialog { background-color: #FAF8F5; }
                QLabel#ReviewTitle { font-size: 15px; font-weight: 700; color: #292524; }
                QLabel#ReviewDesc { font-size: 13px; color: #57534E; line-height: 1.5; }
                QLabel#ReviewHint { font-size: 11px; color: #78716C; }
                QPushButton { 
                    border-radius: 8px; padding: 9px 20px; 
                    font-size: 13px; font-weight: 600; min-width: 120px;
                }
                QPushButton#AcceptBtn {
                    background-color: #059669; color: white; border: none;
                }
                QPushButton#AcceptBtn:hover { background-color: #047857; }
                QPushButton#ReviewBtn {
                    background-color: #F59E0B; color: white; border: none;
                }
                QPushButton#ReviewBtn:hover { background-color: #D97706; }
                QPushButton#RejectBtn {
                    background-color: #E8E4E0; color: #57534E; border: 1px solid #D6D0CA;
                }
                QPushButton#RejectBtn:hover { background-color: #D6D0CA; }
            """)

            dlg_layout = QVBoxLayout(dlg)
            dlg_layout.setSpacing(12)
            dlg_layout.setContentsMargins(24, 20, 24, 20)

            title = QLabel("Low-Similarity Correction Detected")
            title.setObjectName("ReviewTitle")
            dlg_layout.addWidget(title)

            desc = QLabel(
                "The AI-generated correction is very different from your original text "
                f"({sim_float:.0%} similarity). This may indicate a hallucinated or unreliable "
                "result."
            )
            desc.setObjectName("ReviewDesc")
            desc.setWordWrap(True)
            dlg_layout.addWidget(desc)

            hint = QLabel("You can review the changes in the diff panel below before deciding.")
            hint.setObjectName("ReviewHint")
            hint.setWordWrap(True)
            dlg_layout.addWidget(hint)

            dlg_layout.addStretch()

            btn_layout = QHBoxLayout()
            btn_layout.setSpacing(12)

            reject_btn = QPushButton("Reject")
            reject_btn.setObjectName("RejectBtn")
            reject_btn.setCursor(Qt.CursorShape.PointingHandCursor)

            review_btn = QPushButton("Review Diff")
            review_btn.setObjectName("ReviewBtn")
            review_btn.setCursor(Qt.CursorShape.PointingHandCursor)

            accept_btn = QPushButton("Accept Changes")
            accept_btn.setObjectName("AcceptBtn")
            accept_btn.setCursor(Qt.CursorShape.PointingHandCursor)

            btn_layout.addWidget(reject_btn)
            btn_layout.addStretch()
            btn_layout.addWidget(review_btn)
            btn_layout.addWidget(accept_btn)
            dlg_layout.addLayout(btn_layout)

            accept_btn.clicked.connect(lambda: dlg.done(2))
            review_btn.clicked.connect(lambda: dlg.done(1))
            reject_btn.clicked.connect(lambda: dlg.done(0))
            accept_btn.setDefault(False)
            review_btn.setFocus()

            result = dlg.exec()

            if result == 2:
                spans = _find_protected_spans(original)
                apply_corrections_with_diff(
                    original, corrected,
                    start_offset=review_start,
                    protected_spans=spans,
                )
                if comment and comment.strip():
                    try:
                        word_app = get_word_integration()
                        word_app.add_comment(comment.strip())
                    except Exception as e:
                        print(f"Comment insertion failed: {e}")
                self.status_label.setText(f"Low-similarity correction applied (user approved, {sim_float:.0%}).")
                self._set_corrected_for_copy("")
            elif result == 1:
                if comment and comment.strip():
                    cursor = self.diff_text.textCursor()
                    heading_fmt = QTextCharFormat()
                    heading_fmt.setFontWeight(700)
                    heading_fmt.setForeground(QColor("#1A3A2A"))
                    heading_fmt.setFontPointSize(13)
                    cursor.insertText("Reviewer Comment\n", heading_fmt)
                    sep_fmt = QTextCharFormat()
                    sep_fmt.setForeground(QColor("#D6D0CA"))
                    cursor.insertText("─" * 40 + "\n", sep_fmt)
                    comment_fmt = QTextCharFormat()
                    comment_fmt.setForeground(QColor("#57534E"))
                    cursor.insertText(comment.strip() + "\n\n", comment_fmt)
                    sep2_fmt = QTextCharFormat()
                    sep2_fmt.setForeground(QColor("#D6D0CA"))
                    cursor.insertText("─" * 40 + "\n\n", sep2_fmt)
                self.display_diff(original, corrected)
                self.diff_word_count.setText(f"{len(corrected.split())} words")
                QApplication.processEvents()

                follow = QDialog(self)
                follow.setWindowTitle("Decision — Low-Similarity Result")
                follow.setModal(True)
                follow.setFixedSize(420, 180)
                follow.setStyleSheet("""
                    QDialog { background-color: #FAF8F5; }
                    QLabel#FollowTitle { font-size: 14px; font-weight: 700; color: #292524; }
                    QLabel#FollowDesc { font-size: 12px; color: #57534E; }
                    QPushButton { border-radius: 8px; padding: 9px 20px; font-size: 13px; font-weight: 600; min-width: 110px; }
                    QPushButton#FollowAccept { background-color: #059669; color: white; border: none; }
                    QPushButton#FollowAccept:hover { background-color: #047857; }
                    QPushButton#FollowReject { background-color: #E8E4E0; color: #57534E; border: 1px solid #D6D0CA; }
                    QPushButton#FollowReject:hover { background-color: #D6D0CA; }
                """)

                f_layout = QVBoxLayout(follow)
                f_layout.setSpacing(12)
                f_layout.setContentsMargins(24, 20, 24, 20)

                f_title = QLabel("Apply or Discard?")
                f_title.setObjectName("FollowTitle")
                f_layout.addWidget(f_title)

                f_desc = QLabel(
                    f"You have reviewed the diff ({sim_float:.0%} similarity). "
                    "Apply the changes to your document or discard them."
                )
                f_desc.setObjectName("FollowDesc")
                f_desc.setWordWrap(True)
                f_layout.addWidget(f_desc)

                f_layout.addStretch()

                f_btn_layout = QHBoxLayout()
                f_btn_layout.setSpacing(12)

                f_reject = QPushButton("Discard")
                f_reject.setObjectName("FollowReject")
                f_reject.setCursor(Qt.CursorShape.PointingHandCursor)

                f_accept = QPushButton("Apply Changes")
                f_accept.setObjectName("FollowAccept")
                f_accept.setCursor(Qt.CursorShape.PointingHandCursor)

                f_btn_layout.addWidget(f_reject)
                f_btn_layout.addStretch()
                f_btn_layout.addWidget(f_accept)
                f_layout.addLayout(f_btn_layout)

                f_reject.clicked.connect(lambda: follow.done(0))
                f_accept.clicked.connect(lambda: follow.done(1))

                follow_result = follow.exec()

                if follow_result == 1:
                    spans = _find_protected_spans(original)
                    apply_corrections_with_diff(
                        original, corrected,
                        start_offset=review_start,
                        protected_spans=spans,
                    )
                    if comment and comment.strip():
                        try:
                            word_app = get_word_integration()
                            word_app.add_comment(comment.strip())
                        except Exception as e:
                            print(f"Comment insertion failed: {e}")
                    self.status_label.setText(f"Low-similarity correction applied (user approved, {sim_float:.0%}).")
                    self._set_corrected_for_copy("")
                    corrected = ""
                else:
                    self.status_label.setText(
                        f"Correction rejected (low similarity {sim_float:.0%})."
                    )
                    self._set_corrected_for_copy("")
                    corrected = ""
                self.diff_text.clear()
                return
            else:
                self.status_label.setText(
                    f"Correction rejected (low similarity {sim_float:.0%})."
                )
                self._set_corrected_for_copy("")
                corrected = ""

            self.diff_text.clear()
            if result != 2 and corrected:
                self.display_diff(original, corrected)
                self.diff_word_count.setText(f"{len(corrected.split())} words")
            return

        self.status_label.setText(status_text)
        
        self.diff_text.clear()
        if comment and comment.strip():
            cursor = self.diff_text.textCursor()
            heading_fmt = QTextCharFormat()
            heading_fmt.setFontWeight(700)
            heading_fmt.setForeground(QColor("#1A3A2A"))
            heading_fmt.setFontPointSize(13)
            cursor.insertText("Reviewer Comment\n", heading_fmt)
            
            sep_fmt = QTextCharFormat()
            sep_fmt.setForeground(QColor("#D6D0CA"))
            cursor.insertText("─" * 40 + "\n", sep_fmt)
            
            comment_fmt = QTextCharFormat()
            comment_fmt.setForeground(QColor("#57534E"))
            cursor.insertText(comment.strip() + "\n\n", comment_fmt)
            
            sep2_fmt = QTextCharFormat()
            sep2_fmt.setForeground(QColor("#D6D0CA"))
            cursor.insertText("─" * 40 + "\n\n", sep2_fmt)

        if original and corrected:
            self.display_diff(original, corrected)
            word_count = len(corrected.split())
            self.diff_word_count.setText(f"{word_count} words")
        elif "error" in status_text.lower() and not comment:
            self.diff_text.setPlainText(f"Failed: {status_text}")
            self.diff_word_count.setText("")
            self._set_corrected_for_copy("")
        elif not original:
            self.diff_text.setPlainText(status_text)
            self.diff_word_count.setText("")
            self._set_corrected_for_copy("")

    def _show_word_unavailable(self, status_text: str, word_running: bool) -> None:
        msg = QMessageBox(self)
        msg.setWindowTitle("Microsoft Word Not Running" if not word_running else "No Word Document Open")
        msg.setIcon(QMessageBox.Icon.Warning)
        if not word_running:
            msg.setText("Microsoft Word is not running.")
            if platform.system() == "Windows":
                msg.setInformativeText("Start Microsoft Word, open your document, then try proofreading again.")
            else:
                msg.setInformativeText("Open Microsoft Word from Applications, open your document, then try proofreading again.")
        else:
            msg.setText("No Word document is open.")
            msg.setInformativeText("Open a document in Microsoft Word, then try proofreading again.")
        open_word_btn = msg.addButton("Open Word", QMessageBox.ButtonRole.ActionRole)
        msg.addButton(QMessageBox.StandardButton.Ok)
        msg.exec()
        if msg.clickedButton() == open_word_btn:
            self._launch_word()
        self.status_label.setText(status_text)
        self.diff_text.clear()
        self.diff_word_count.setText("")
        self._set_corrected_for_copy("")

    @staticmethod
    def _launch_word() -> None:
        try:
            if platform.system() == "Darwin":
                subprocess.Popen(["open", "-a", "Microsoft Word"])
            elif platform.system() == "Windows":
                subprocess.Popen(["start", "winword"], shell=True)
        except Exception as e:
            print(f"Could not launch Word: {e}")

    def _handle_generic_result(
        self,
        status_text: str,
        original: str,
        corrected: str,
        comment: str,
    ) -> None:
        target = getattr(self.worker, "generic_target", None) or {}
        app_name = target.get("name") or "the active app"
        if original:
            self.last_generic_target = target

        if status_text.startswith("REVIEW_NEEDED:"):
            sim = status_text.split(":", 1)[1] if ":" in status_text else "0"
            self.status_label.setText(f"Low similarity ({sim}) — review before applying.")
            self._show_toast("Review changes — open ByteProof to review.", kind="warning")
            self._show_generic_diff(original, corrected, comment, app_name)
            return

        lowered = status_text.lower()
        if status_text.startswith("Error") or "no text" in lowered or "too short" in lowered:
            self.status_label.setText(
                status_text + " — Tray menu → Copy Capture Diagnostics"
            )
            self.diff_text.setPlainText(status_text)
            self.diff_word_count.setText("")
            self._set_corrected_for_copy("")
            self._show_toast(status_text, kind="error")
            return

        if status_text == "No changes suggested.":
            self.status_label.setText(f"No changes needed in {app_name}.")
            self._show_toast(f"No changes needed in {app_name}.", kind="success")
            return

        if status_text != "Polished.":
            self.status_label.setText(status_text)
            self.diff_text.setPlainText(status_text)
            self.diff_word_count.setText("")
            self._show_toast(status_text, kind="error")
            return

        auto_apply = self.settings.get("general", {}).get("auto_apply", True)
        if auto_apply:
            self.pending_generic_preview = {
                "original": original,
                "corrected": corrected,
                "comment": comment,
                "app_name": app_name,
            }
            self._apply_generic_text(original, corrected, target)
        else:
            self._show_toast("Ready for review — open ByteProof to apply.", kind="success")
            self._show_generic_diff(
                original,
                corrected,
                comment,
                app_name,
                show_apply_button=True,
            )

    def _show_generic_diff(
        self,
        original: str,
        corrected: str,
        comment: str,
        app_name: str,
        show_apply_button: bool = True,
    ) -> None:
        self.diff_text.clear()
        if comment and comment.strip():
            cursor = self.diff_text.textCursor()
            heading_fmt = QTextCharFormat()
            heading_fmt.setFontWeight(700)
            heading_fmt.setForeground(QColor("#1A3A2A"))
            heading_fmt.setFontPointSize(13)
            cursor.insertText("Reviewer Comment\n", heading_fmt)
            sep_fmt = QTextCharFormat()
            sep_fmt.setForeground(QColor("#D6D0CA"))
            cursor.insertText("─" * 40 + "\n", sep_fmt)
            comment_fmt = QTextCharFormat()
            comment_fmt.setForeground(QColor("#57534E"))
            cursor.insertText(comment.strip() + "\n\n", comment_fmt)
            sep2_fmt = QTextCharFormat()
            sep2_fmt.setForeground(QColor("#D6D0CA"))
            cursor.insertText("─" * 40 + "\n\n", sep2_fmt)
        self.display_diff(original, corrected)
        self.diff_word_count.setText(f"{len(corrected.split())} words")
        if show_apply_button:
            target = getattr(self.worker, "generic_target", None) or {}
            self.pending_generic_apply = {
                "original": original,
                "corrected": corrected,
                "target": target,
            }
            self.apply_btn.setText(f"Apply to {app_name}")
            self.apply_btn.setVisible(True)
            self.apply_btn.setEnabled(True)
        else:
            self.pending_generic_apply = None
            self.apply_btn.setVisible(False)

    def _apply_pending_generic(self) -> None:
        pending = self.pending_generic_apply
        if not pending:
            return
        self._apply_generic_text(
            pending["original"],
            pending["corrected"],
            pending["target"],
        )

    def _apply_generic_text(
        self,
        original: str,
        corrected: str,
        target: dict[str, Any],
    ) -> None:
        app_name = target.get("name") or "the app"
        self.status_label.setText(f"Applying to {app_name}…")
        self._generic_apply_pending = True
        self._show_toast(f"Applying to {app_name}…", kind="processing")
        self.apply_btn.setEnabled(False)
        worker = GenericApplyWorker(original, corrected, target)
        self.generic_apply_worker = worker
        worker.done.connect(self._on_generic_apply_done)
        worker.start()

    def _on_generic_apply_done(self, ok: bool, message: str) -> None:
        self._generic_apply_pending = False
        self.apply_btn.setVisible(False)
        self.apply_btn.setEnabled(True)
        self.pending_generic_apply = None
        preview = getattr(self, "pending_generic_preview", None)
        self.pending_generic_preview = None
        self.status_label.setText(message)
        if not ok:
            if preview:
                self._show_generic_diff(
                    preview["original"],
                    preview["corrected"],
                    preview["comment"],
                    preview["app_name"],
                    show_apply_button=True,
                )
            self._show_toast(message, kind="error")
        elif preview:
            # Keep the tracked-changes-style diff ready in the Proposed Changes
            # panel without interrupting the user's flow in the other app.
            self._show_generic_diff(
                preview["original"],
                preview["corrected"],
                preview["comment"],
                preview["app_name"],
                show_apply_button=False,
            )
            self._show_toast(message, kind="success")
        else:
            self._show_toast(message, kind="success")

    def _copy_capture_diagnostics(self) -> None:
        import json

        from .generic_editing import capture_diagnostics

        try:
            data = capture_diagnostics()
        except Exception as e:
            data = {"error": str(e)}
        text = json.dumps(data, indent=2, ensure_ascii=False, default=str)
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
        self._show_toast("Capture diagnostics copied to clipboard.", kind="success")

    def _open_support_folder(self) -> None:
        from .settings import get_app_support_dir

        try:
            folder = get_app_support_dir()
            os.makedirs(folder, exist_ok=True)
            subprocess.Popen(["open", folder])
        except Exception as e:
            print(f"Could not open support folder: {e}")

    def handle_error(self, error_msg: str) -> None:
        self.status_dot.setStyleSheet("background-color: #B91C1C; border-radius: 4px;")
        self.status_label.setText(f"Error: {error_msg}")
        self._set_corrected_for_copy("")
        self._show_toast(f"Error: {error_msg}", kind="error")

    def task_finished(self) -> None:
        self.run_btn.setEnabled(True)
        self.status_dot.setStyleSheet("background-color: #059669; border-radius: 4px;")
        if hasattr(self, 'tray_icon') and hasattr(self, 'normal_icon') and not self.normal_icon.isNull():
            self.tray_icon.setIcon(self.normal_icon)
        if self.toast.is_processing() and not self._generic_apply_pending:
            status_text = self.status_label.text()
            message = status_text if len(status_text) <= 72 else status_text[:69] + "…"
            kind = "error" if status_text.startswith("Error") else "success"
            self._show_toast(message, kind=kind)

    def open_settings(self):
        try:
            dialog = SettingsDialog(self.current_settings(), self)
            self._active_settings_dialog = dialog
            dialog.finished.connect(
                lambda _result: setattr(self, "_active_settings_dialog", None)
            )
            
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            
            updated = dialog.get_settings()

            self.settings = updated
            
            keep_on_top = updated.get("general", {}).get("keep_on_top", True)
            current_flags = self.windowFlags()
            has_on_top = bool(current_flags & Qt.WindowType.WindowStaysOnTopHint)
            
            if keep_on_top != has_on_top:
                was_visible = self.isVisible()
                if was_visible:
                    self.hide()
                    
                if keep_on_top:
                    self.setWindowFlags(current_flags | Qt.WindowType.WindowStaysOnTopHint)
                else:
                    self.setWindowFlags(current_flags & ~Qt.WindowType.WindowStaysOnTopHint)
                    
                if was_visible:
                    self.show()
                    self.raise_()
                    self.activateWindow()
            
            save_runtime_settings(updated)
            launch_wanted = updated.get("general", {}).get("launch_at_login", False)
            if launch_wanted:
                if not set_launch_at_login(True):
                    QMessageBox.warning(
                        self,
                        "Launch at Login",
                        "ByteProof could not be set to launch at login.\n\n"
                        "You can still launch ByteProof manually.",
                    )
            else:
                set_launch_at_login(False)
            self.status_label.setText("Settings saved.")
            print(f"Settings saved. New Temp: {updated['general']['temperature']}")
            
            self._update_proofread_button()
            self._setup_hotkeys()
        except Exception as e:
            print(f"Error opening settings: {e}")
            QMessageBox.critical(self, "Error", f"Could not open settings: {e}")

    def display_diff(self, original: str, corrected: str) -> None:
        matcher = difflib.SequenceMatcher(None, original, corrected, autojunk=False)
        cursor = self.diff_text.textCursor()
        
        del_fmt = QTextCharFormat()
        del_fmt.setBackground(QColor("#FEF2F2"))
        del_fmt.setForeground(QColor("#991B1B"))
        del_fmt.setFontStrikeOut(True)
        
        ins_fmt = QTextCharFormat()
        ins_fmt.setBackground(QColor("#ECFDF5"))
        ins_fmt.setForeground(QColor("#065F46"))
        
        normal_fmt = QTextCharFormat()
        normal_fmt.setForeground(QColor("#57534E"))

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'replace':
                cursor.insertText(original[i1:i2], del_fmt)
                cursor.insertText(corrected[j1:j2], ins_fmt)
            elif tag == 'delete':
                cursor.insertText(original[i1:i2], del_fmt)
            elif tag == 'insert':
                cursor.insertText(corrected[j1:j2], ins_fmt)
            elif tag == 'equal':
                cursor.insertText(original[i1:i2], normal_fmt)


def _find_owner_window(widget: QWidget) -> ProofreaderApp | None:
    """Return the nearest ProofreaderApp ancestor for worker storage.

    QDialog.window() returns the dialog itself, so workers must be anchored to
    the main window through the parent chain; otherwise a running QThread can
    be garbage-collected and Qt aborts with "QThread: Destroyed while thread
    is still running".
    """
    current: QWidget | None = widget
    while current is not None:
        if isinstance(current, ProofreaderApp):
            return current
        current = current.parentWidget()
    return None

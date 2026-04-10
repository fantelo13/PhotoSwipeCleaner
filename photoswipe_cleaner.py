#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import random
import shutil
import time
from pathlib import Path
from typing import List, Optional, Dict, Any

from PySide6.QtCore import Qt, QRect, QPoint, QSize
from PySide6.QtGui import QPixmap, QAction, QKeySequence, QPainter, QFont, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QLabel,
    QFileDialog,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QToolBar,
    QMessageBox,
    QStatusBar,
)

IMG_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp", ".heic", ".heif"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}
BACKUP_DIRNAME = ".pswipe_last_backup"


def unique_name(target_dir: Path, base_name: str) -> Path:
    candidate = target_dir / base_name
    if not candidate.exists():
        return candidate

    stem, suffix = os.path.splitext(base_name)
    i = 1
    while True:
        candidate = target_dir / f"{stem}__pswipe_{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    units = ["B", "KB", "MB", "GB"]
    idx = 0
    while size >= 1024 and idx < len(units) - 1:
        size /= 1024
        idx += 1
    return f"{size:.1f} {units[idx]}"


def scan_images_recursive(root: Path) -> tuple[List[str], int]:
    files: List[str] = []
    skipped_videos = 0
    backup_dir = root / BACKUP_DIRNAME

    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if backup_dir in p.parents:
            continue

        ext = p.suffix.lower()
        if ext in IMG_EXT:
            files.append(str(p.relative_to(root)))
        elif ext in VIDEO_EXT:
            skipped_videos += 1

    return files, skipped_videos


class ImageView(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(700, 450)
        self._pix: Optional[QPixmap] = None
        self._dragging = False
        self._drag_start: Optional[QPoint] = None
        self._offset_x = 0
        self._overlay_text: Optional[str] = None
        self._image_token = 0
        self._press_token = None

    def set_image(self, pix: QPixmap):
        self._pix = pix
        self._offset_x = 0
        self._overlay_text = None
        self._image_token += 1
        self.setText("")
        self.update()

    def clear_image(self, text: str):
        self._pix = None
        self._offset_x = 0
        self._overlay_text = None
        self.setText(text)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._pix:
            return

        painter = QPainter(self)
        scaled = self._pix.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        x = (self.width() - scaled.width()) // 2 + self._offset_x
        y = (self.height() - scaled.height()) // 2
        painter.drawPixmap(QPoint(x, y), scaled)

        if self._overlay_text:
            painter.setOpacity(0.75)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(Qt.white)
            font = QFont()
            font.setPointSize(28)
            font.setBold(True)
            painter.setFont(font)
            rect = QRect(0, 0, self.width(), 80)
            painter.drawText(rect, Qt.AlignHCenter | Qt.AlignVCenter, self._overlay_text)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_start = e.position().toPoint()
            self._overlay_text = None
            self._press_token = self._image_token
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._dragging and self._drag_start is not None:
            dx = e.position().toPoint().x() - self._drag_start.x()
            self._offset_x = int(dx)
            self._overlay_text = "KEEP" if dx > 0 else "DELETE"
            self.update()
            e.accept()
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._dragging and self._drag_start is not None:
            dx = e.position().toPoint().x() - self._drag_start.x()
            threshold = 120
            self._dragging = False
            self._drag_start = None
            self._offset_x = 0
            self._overlay_text = None

            if self._press_token != self._image_token:
                self.update()
                e.accept()
                return

            self.update()
            win = self.window()

            if dx <= -threshold and hasattr(win, "request_delete"):
                win.request_delete()
                e.accept()
                return
            if dx >= threshold and hasattr(win, "request_keep"):
                win.request_keep()
                e.accept()
                return

            e.accept()
        else:
            super().mouseReleaseEvent(e)


class PhotoSwipeCleaner(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PhotoSwipe Cleaner")
        self.resize(1180, 820)

        self.folder: Optional[Path] = None
        self.files_order: List[str] = []
        self.index = 0
        self.last_action: Optional[Dict[str, Any]] = None
        self._finished_dialog_shown = False
        self.skipped_videos = 0
        self.kept_count = 0
        self.deleted_count = 0

        self.image_label = ImageView(self)

        self.info_label = QLabel("No folder selected")
        self.info_label.setAlignment(Qt.AlignCenter)

        self.progress_label = QLabel("")
        self.progress_label.setAlignment(Qt.AlignCenter)

        self.keep_btn = QPushButton("Keep (→ / K)")
        self.del_btn = QPushButton("Delete (← / D)")
        self.undo_btn = QPushButton("Undo (Z)")
        self.restart_btn = QPushButton("Reshuffle (R)")
        self.exit_btn = QPushButton("Exit (E)")

        self.keep_btn.clicked.connect(self.request_keep)
        self.del_btn.clicked.connect(self.request_delete)
        self.undo_btn.clicked.connect(self.undo_last)
        self.restart_btn.clicked.connect(self.reshuffle_and_restart)
        self.exit_btn.clicked.connect(self.exit_app)

        controls = QHBoxLayout()
        controls.addWidget(self.del_btn)
        controls.addWidget(self.undo_btn)
        controls.addWidget(self.restart_btn)
        controls.addWidget(self.exit_btn)
        controls.addWidget(self.keep_btn)

        layout = QVBoxLayout()
        layout.addWidget(self.image_label, 1)
        layout.addWidget(self.info_label)
        layout.addWidget(self.progress_label)
        layout.addLayout(controls)

        host = QWidget()
        host.setLayout(layout)
        self.setCentralWidget(host)

        toolbar = QToolBar("Main")
        toolbar.setIconSize(QSize(18, 18))
        self.addToolBar(toolbar)

        act_open = QAction("Open Folder", self)
        act_open.triggered.connect(self.choose_folder)
        toolbar.addAction(act_open)

        act_reveal = QAction("Reveal Current", self)
        act_reveal.triggered.connect(self.reveal_current)
        toolbar.addAction(act_reveal)

        act_help = QAction("Help", self)
        act_help.triggered.connect(self.show_help)
        toolbar.addAction(act_help)

        self._build_menu()
        self.setStatusBar(QStatusBar())

        QShortcut(QKeySequence(Qt.Key_Right), self, activated=self.request_keep)
        QShortcut(QKeySequence(Qt.Key_K), self, activated=self.request_keep)
        QShortcut(QKeySequence(Qt.Key_Left), self, activated=self.request_delete)
        QShortcut(QKeySequence(Qt.Key_D), self, activated=self.request_delete)
        QShortcut(QKeySequence(Qt.Key_R), self, activated=self.reshuffle_and_restart)
        QShortcut(QKeySequence(Qt.Key_Z), self, activated=self.undo_last)
        QShortcut(QKeySequence(Qt.Key_E), self, activated=self.exit_app)
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.choose_folder)
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self.reshuffle_and_restart)

        if len(sys.argv) > 1:
            p = Path(sys.argv[1]).expanduser().resolve()
            if p.exists() and p.is_dir():
                self.load_folder(p)

    def _build_menu(self):
        menu = self.menuBar()

        file_menu = menu.addMenu("File")

        open_action = QAction("Open Folder", self)
        open_action.triggered.connect(self.choose_folder)
        file_menu.addAction(open_action)

        reshuffle_action = QAction("Reshuffle", self)
        reshuffle_action.triggered.connect(self.reshuffle_and_restart)
        file_menu.addAction(reshuffle_action)

        file_menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.exit_app)
        file_menu.addAction(exit_action)

        help_menu = menu.addMenu("Help")
        shortcuts_action = QAction("Shortcuts", self)
        shortcuts_action.triggered.connect(self.show_help)
        help_menu.addAction(shortcuts_action)

    def show_help(self):
        QMessageBox.information(
            self,
            "Shortcuts",
            "→ or K = Keep\n"
            "← or D = Delete\n"
            "Z = Undo last action\n"
            "E = Exit\n"
            "Ctrl+O = Open folder\n"
            "Ctrl+R = Reshuffle\n"
            "\nSwipe right to keep, swipe left to delete."
        )

    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Select photo folder")
        if path:
            self.load_folder(Path(path))

    def load_folder(self, folder: Path):
        self.folder = folder
        self.statusBar().showMessage(f"Loading folder: {folder}")

        files, skipped_videos = scan_images_recursive(folder)
        if not files:
            QMessageBox.information(self, "Empty", "No eligible images found in this folder.")
            return

        random.seed(hash(folder) ^ int(time.time()))
        random.shuffle(files)

        self.files_order = files
        self.index = 0
        self.last_action = None
        self._finished_dialog_shown = False
        self.skipped_videos = skipped_videos
        self.kept_count = 0
        self.deleted_count = 0

        self._clear_backup_dir()
        self.show_current()

    def _backup_dir(self) -> Path:
        assert self.folder is not None
        d = self.folder / BACKUP_DIRNAME
        d.mkdir(exist_ok=True)
        return d

    def _clear_backup_dir(self):
        if not self.folder:
            return
        d = self._backup_dir()
        for p in d.iterdir():
            try:
                if p.is_file():
                    p.unlink()
            except Exception:
                pass

    def _commit_pending_delete(self):
        if not self.last_action:
            return
        if self.last_action.get("type") == "delete":
            backup_path = self.last_action.get("backup_path")
            if backup_path:
                try:
                    Path(backup_path).unlink(missing_ok=True)
                except Exception:
                    pass
        self.last_action = None

    def exit_app(self):
        self._commit_pending_delete()
        self.close()

    def current_path(self) -> Optional[Path]:
        if not self.folder or not self.files_order:
            return None
        if not (0 <= self.index < len(self.files_order)):
            return None
        return self.folder / self.files_order[self.index]

    def _advance(self, step: int):
        self.index += step
        if self.index >= len(self.files_order):
            self.index = len(self.files_order)
        elif self.index < 0:
            self.index = 0
        self.show_current()

    def _update_labels(self, p: Optional[Path]):
        total = len(self.files_order)
        current = min(self.index + 1, total) if total > 0 and self.index < total else total

        self.progress_label.setText(
            f"Current: {current}/{total} | Kept: {self.kept_count} | Deleted: {self.deleted_count} | Videos skipped: {self.skipped_videos}"
        )

        if p and p.exists():
            self.info_label.setText(f"{p.name} | {p.suffix.lower()} | {format_size(p.stat().st_size)}")
        else:
            self.info_label.setText("")

    def show_current(self):
        p = self.current_path()
        if not p:
            self._update_labels(None)
            self.image_label.clear_image("Finished!\nAll images processed.")
            if not self._finished_dialog_shown:
                self._finished_dialog_shown = True
                self.on_finished_list()
            return

        pix = QPixmap(str(p))
        if pix.isNull():
            try:
                self.files_order.pop(self.index)
            except Exception:
                pass
            if self.index >= len(self.files_order):
                self.index = len(self.files_order)
            self.show_current()
            return

        self.image_label.set_image(pix)
        self._update_labels(p)
        self.statusBar().showMessage(f"{p.name} • {self.index+1}/{len(self.files_order)}")

    def request_keep(self):
        self._commit_pending_delete()
        p = self.current_path()
        if not p:
            return
        self.last_action = {"type": "keep", "prev_index": self.index}
        self.kept_count += 1
        self._advance(1)

    def request_delete(self):
        self._commit_pending_delete()

        p = self.current_path()
        if not p:
            return

        rel = self.files_order[self.index]
        backup_dir = self._backup_dir()
        backup_target = unique_name(backup_dir, Path(rel).name)

        try:
            shutil.move(str(p), str(backup_target))
        except Exception as e:
            QMessageBox.critical(self, "Delete failed", f"Could not move file to temporary backup:\n{e}")
            return

        try:
            self.files_order.pop(self.index)
        except Exception:
            pass

        self.last_action = {
            "type": "delete",
            "orig_rel": rel,
            "backup_path": str(backup_target),
        }
        self.deleted_count += 1

        if self.index >= len(self.files_order):
            self.index = len(self.files_order)

        self.show_current()

    def undo_last(self):
        if not self.last_action:
            self.statusBar().showMessage("Nothing to undo")
            return

        action = self.last_action
        self.last_action = None

        if action.get("type") == "keep":
            if self.index > 0:
                self.index -= 1
                self.kept_count = max(0, self.kept_count - 1)
            self.show_current()
            return

        if action.get("type") == "delete":
            backup_path = Path(action.get("backup_path", ""))
            orig_rel = action.get("orig_rel", "")

            if not backup_path.exists():
                self.statusBar().showMessage("Nothing to restore")
                return

            dest = self.folder / orig_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                dest = unique_name(dest.parent, dest.name)

            try:
                shutil.move(str(backup_path), str(dest))
            except Exception as e:
                QMessageBox.critical(self, "Undo failed", f"Could not restore file:\n{e}")
                return

            insert_pos = max(min(self.index, len(self.files_order)), 0)
            self.files_order.insert(insert_pos, str(dest.relative_to(self.folder)))
            self.index = insert_pos
            self.deleted_count = max(0, self.deleted_count - 1)
            self.show_current()
            return

        self.statusBar().showMessage("Unknown action")

    def on_finished_list(self):
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Information)
        msg.setWindowTitle("Finished")
        msg.setText("You reached the end of the list.")
        msg.setInformativeText("Do you want to reshuffle the remaining photos and start again, or exit?")

        btn_shuffle = msg.addButton("Reshuffle and restart", QMessageBox.AcceptRole)
        btn_exit = msg.addButton("Exit", QMessageBox.RejectRole)
        msg.setDefaultButton(btn_shuffle)
        msg.exec()

        clicked = msg.clickedButton()
        if clicked == btn_shuffle:
            self._commit_pending_delete()
            self.reshuffle_and_restart()
        elif clicked == btn_exit:
            self._commit_pending_delete()
            self.close()

    def reshuffle_and_restart(self):
        if not self.folder:
            return

        files, skipped_videos = scan_images_recursive(self.folder)
        if not files:
            QMessageBox.warning(self, "Warning", "No images found to reshuffle.")
            return

        random.seed(hash(self.folder) ^ int(time.time()))
        random.shuffle(files)

        self.files_order = files
        self.index = 0
        self.last_action = None
        self._finished_dialog_shown = False
        self.skipped_videos = skipped_videos
        self.kept_count = 0
        self.deleted_count = 0
        self._clear_backup_dir()
        self.show_current()

    def reveal_current(self):
        p = self.current_path()
        if not p:
            return
        if sys.platform == "darwin":
            os.system(f'open -R "{p}"')
        elif os.name == "nt":
            os.system(f'explorer /select,"{p}"')
        else:
            os.system(f'xdg-open "{p.parent}"')


def main():
    try:
        import signal
        signal.signal(signal.SIGINT, signal.SIG_DFL)
    except Exception:
        pass

    app = QApplication(sys.argv)
    w = PhotoSwipeCleaner()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
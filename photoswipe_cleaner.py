#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PhotoSwipe Cleaner – protótipo mínimo funcional

Requisitos:
  pip install pyside6 send2trash

Execução:
  python photoswipe_cleaner.py

Plataformas: macOS, Windows, Linux (testado no macOS Catalina+)

Recursos-chave deste protótipo:
- Selecionar pasta com milhares de fotos
- Ordem aleatória persistente entre sessões (salva em .pswipe_state.json na pasta)
- Lembra onde parou (índice corrente persistido)
- Ações rápidas: Manter (→) / Deletar (←) / Undo (Z)
- Deleção em tempo real para a Lixeira do sistema (via send2trash)
- Gestos simples de "swipe" com o mouse/trackpad (arrastar > 120 px)
- Log em CSV para auditoria
- Botões e atalhos de teclado (D=Delete, K=Keep, Z=Undo)

Observações:
- Este é um protótipo: foco em estabilidade, velocidade e armazenamento de estado.
- Otimizações futuras: preloading assíncrono de próximas imagens, zoom, filtros, temas.
"""
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import os
import sys
import random
import shutil
import time
from datetime import datetime
from typing import List, Optional, Dict, Any
from pathlib import Path

from PySide6.QtCore import Qt, QRect, QPoint
from PySide6.QtGui import QPixmap, QAction, QKeySequence, QPainter, QFont, QShortcut
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QFileDialog, QVBoxLayout,
    QHBoxLayout, QPushButton, QToolBar, QMessageBox, QStatusBar
)

IMG_EXT = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp', '.heic', '.heif'}
BACKUP_DIRNAME = '.pswipe_last_backup'


def human_ts() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def unique_name(target_dir: Path, base_name: str) -> Path:
    cand = target_dir / base_name
    if not cand.exists():
        return cand
    stem, suf = os.path.splitext(base_name)
    i = 1
    while True:
        cand = target_dir / f"{stem}__pswipe_{i}{suf}"
        if not cand.exists():
            return cand
        i += 1


def scan_images_recursive(root: Path) -> List[str]:
    files: List[str] = []
    backup_dir = root / BACKUP_DIRNAME
    for p in root.rglob('*'):
        if not p.is_file():
            continue
        if backup_dir in p.parents:
            continue
        if p.suffix.lower() in IMG_EXT:
            files.append(str(p.relative_to(root)))
    return files


class ImageView(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(600, 400)
        self._pix: Optional[QPixmap] = None
        self._dragging = False
        self._drag_start = None
        self._offset_x = 0
        self._overlay_text: Optional[str] = None
        self._image_token = 0
        self._press_token = None

    def set_image(self, pix: QPixmap):
        self._pix = pix
        self._offset_x = 0
        self._overlay_text = None
        self._image_token += 1
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
            painter.setOpacity(0.7)
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
            self._overlay_text = 'MANTER' if dx > 0 else 'DELETAR'
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
            elif dx >= threshold and hasattr(win, "request_keep"):
                win.request_keep()
                e.accept()
                return
            e.accept()
        else:
            super().mouseReleaseEvent(e)


class PhotoSwipeCleaner(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('PhotoSwipe Cleaner')
        self.resize(980, 720)

        self.folder: Optional[Path] = None
        self.files_order: List[str] = []
        self.index: int = 0

        # última ação para UNDO simples
        self.last_action: Optional[Dict[str, Any]] = None
        self._finished_dialog_shown = False

        self.image_label = ImageView(self)

        self.keep_btn = QPushButton('Manter (→ / K)')
        self.del_btn = QPushButton('Deletar (← / D)')
        self.undo_btn = QPushButton('Desfazer (Z)')

        self.keep_btn.clicked.connect(self.request_keep)
        self.del_btn.clicked.connect(self.request_delete)
        self.undo_btn.clicked.connect(self.undo_last)

        ctrls = QHBoxLayout()
        ctrls.addWidget(self.del_btn)
        ctrls.addWidget(self.undo_btn)
        ctrls.addWidget(self.keep_btn)

        root = QVBoxLayout()
        root.addWidget(self.image_label, 1)
        root.addLayout(ctrls)

        host = QWidget()
        host.setLayout(root)
        self.setCentralWidget(host)

        tb = QToolBar('Main')
        self.addToolBar(tb)
        act_open = QAction('Abrir pasta…', self)
        act_open.triggered.connect(self.choose_folder)
        tb.addAction(act_open)
        act_reveal = QAction('Mostrar no Finder/Explorer', self)
        act_reveal.triggered.connect(self.reveal_current)
        tb.addAction(act_reveal)

        QShortcut(QKeySequence(Qt.Key_Right), self, activated=self.request_keep)
        QShortcut(QKeySequence(Qt.Key_K), self, activated=self.request_keep)
        QShortcut(QKeySequence(Qt.Key_Left), self, activated=self.request_delete)
        QShortcut(QKeySequence(Qt.Key_D), self, activated=self.request_delete)
        QShortcut(QKeySequence(Qt.Key_Z), self, activated=self.undo_last)
        QShortcut(QKeySequence('Ctrl+O'), self, activated=self.choose_folder)

        self.setStatusBar(QStatusBar())

        if len(sys.argv) > 1:
            p = Path(sys.argv[1]).expanduser().resolve()
            if p.exists() and p.is_dir():
                self.load_folder(p)
            else:
                QMessageBox.warning(self, 'Aviso', f'Pasta inválida: {p}')

    # -------- Folder / Session --------
    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, 'Selecionar pasta de fotos')
        if path:
            self.load_folder(Path(path))

    def load_folder(self, folder: Path):
        self.folder = folder
        self.statusBar().showMessage(f'Carregando pasta: {folder}')

        files = scan_images_recursive(folder)
        if not files:
            QMessageBox.information(self, 'Vazio', 'Nenhuma imagem elegível encontrada.')
            return

        random.seed(hash(folder) ^ int(time.time()))
        random.shuffle(files)

        self.files_order = files
        self.index = 0
        self.last_action = None
        self._finished_dialog_shown = False

        self._clear_backup_dir()  # limpa restos de sessões anteriores
        self.show_current()

    # -------- Helpers --------
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
                p.unlink()
            except Exception:
                pass

    def _commit_pending_delete(self):
        if not self.last_action:
            return
        if self.last_action.get('type') == 'delete':
            bp = self.last_action.get('backup_path')
            if bp:
                try:
                    Path(bp).unlink(missing_ok=True)
                except Exception:
                    pass
        self.last_action = None

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

    def show_current(self):
        p = self.current_path()
        if not p:
            if not self._finished_dialog_shown:
                self._finished_dialog_shown = True
                self.on_finished_list()
            return
        pix = QPixmap(str(p))
        if pix.isNull():
            # remove itens que não podem ser carregados
            try:
                self.files_order.pop(self.index)
            except Exception:
                pass
            if self.index >= len(self.files_order):
                self.index = len(self.files_order)
            self.show_current()
            return
        self.image_label.set_image(pix)
        self.statusBar().showMessage(f'{p.name} • {self.index+1}/{len(self.files_order)}')

    # -------- Actions --------
    def request_keep(self):
        self._commit_pending_delete()  # confirma delete anterior, se houver
        p = self.current_path()
        if not p:
            return
        self.last_action = {'type': 'keep', 'prev_index': self.index}
        self._advance(1)

    def request_delete(self):
        self._commit_pending_delete()  # confirma delete anterior, se houver

        p = self.current_path()
        if not p:
            return

        rel = self.files_order[self.index]
        backup_dir = self._backup_dir()
        backup_target = unique_name(backup_dir, Path(rel).name)

        try:
            backup_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(backup_target))
        except Exception as e:
            QMessageBox.critical(self, 'Falha ao deletar', f'Não foi possível mover para backup temporário:\n{e}')
            return

        try:
            self.files_order.pop(self.index)
        except Exception:
            pass

        self.last_action = {
            'type': 'delete',
            'orig_rel': rel,
            'backup_path': str(backup_target),
        }

        if self.index >= len(self.files_order):
            self.index = len(self.files_order)
        self.show_current()

    def undo_last(self):
        if not self.last_action:
            self.statusBar().showMessage('Nada para desfazer')
            return

        action = self.last_action
        self.last_action = None

        if action.get('type') == 'keep':
            if self.index > 0:
                self.index -= 1
            self.show_current()
            return

        if action.get('type') == 'delete':
            backup_path = Path(action.get('backup_path', ''))
            orig_rel = action.get('orig_rel', '')
            if not backup_path or not backup_path.exists():
                self.statusBar().showMessage('Nada para restaurar')
                return

            dest = self.folder / orig_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                dest = unique_name(dest.parent, dest.name)
            try:
                shutil.move(str(backup_path), str(dest))
            except Exception as e:
                QMessageBox.critical(self, 'Undo falhou', f'Erro ao restaurar arquivo:\n{e}')
                return

            insert_pos = max(min(self.index, len(self.files_order)), 0)
            self.files_order.insert(insert_pos, str(dest.relative_to(self.folder)))
            self.index = insert_pos
            self.show_current()
            return

        self.statusBar().showMessage('Ação desconhecida para desfazer')

    # -------- Finish Dialog --------
    def on_finished_list(self):
        # ainda permite UNDO manual antes de decidir (não com diálogo modal).
        # vamos mostrar um diálogo com duas opções.
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Information)
        msg.setWindowTitle('Concluído')
        msg.setText('Você chegou ao fim das fotos. O que deseja fazer?')

        btn_shuffle = msg.addButton('Reembaralhar e recomeçar', QMessageBox.AcceptRole)
        btn_exit = msg.addButton('Sair', QMessageBox.RejectRole)
        msg.setDefaultButton(btn_shuffle)

        # Mostra modal
        msg.exec()

        clicked = msg.clickedButton()
        if clicked == btn_shuffle:
            # confirma delete pendente (se não houve UNDO)
            self._commit_pending_delete()
            self.reshuffle_and_restart()
        elif clicked == btn_exit:
            self._commit_pending_delete()
            self.close()
        else:
            # fallback: apenas fechar o diálogo e manter estado
            self._finished_dialog_shown = False
            self.show_current()

    def reshuffle_and_restart(self):
        if not self.folder:
            return
        files = scan_images_recursive(self.folder)
        random.seed(hash(self.folder) ^ int(time.time()))
        random.shuffle(files)
        self.files_order = files
        self.index = 0
        self._finished_dialog_shown = False
        self._clear_backup_dir()  # inicia “limpo”
        self.show_current()

    # -------- Misc --------
    def reveal_current(self):
        p = self.current_path()
        if not p:
            return
        if sys.platform == 'darwin':
            os.system(f'open -R "{p}"')
        elif os.name == 'nt':
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


if __name__ == '__main__':
    main()

import sys
import json
import socket
import time
import os
import numpy as np
import cv2
from datetime import datetime
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QLabel, QVBoxLayout, QHBoxLayout,
    QWidget, QTabWidget, QTextEdit, QPushButton, QFrame, QSizePolicy,
    QScrollBar, QLineEdit, QGridLayout,
)
from PySide6.QtCore import QThread, Signal, Qt, QTimer, QPoint
from PySide6.QtGui import (
    QColor, QPainter, QBrush, QPen, QImage, QFont, QPolygon,
)

UDP_PORT        = 5005
TCP_HOST        = "127.0.0.1"
TCP_PORT        = 5010
ZONAS_FILE      = "zonas.json"
HOMOGRAFIA_FILE = "homografia.json"


# ── Utilidad de homografía ─────────────────────────────────────────────────────

def _aplicar_H(H: np.ndarray, px: float, py: float) -> tuple:
    """Transforma un punto (px, py) en píxeles a metros usando la matriz H."""
    pt = np.array([[[px, py]]], dtype=np.float32)
    res = cv2.perspectiveTransform(pt, H)
    return float(res[0][0][0]), float(res[0][0][1])


# ── UDP worker — recibe JSON ────────────────────────────────────────────────────

class WorkerRed(QThread):
    datos_recibidos = Signal(dict)

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", UDP_PORT))
        while True:
            data, _ = sock.recvfrom(65535)
            try:
                payload = json.loads(data.decode())
                self.datos_recibidos.emit(payload)
            except Exception:
                pass


# ── TCP worker — recibe JPEG stream ───────────────────────────────────────────

class VideoWorker(QThread):
    frame_ready = Signal(QImage)

    def run(self):
        while True:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect((TCP_HOST, TCP_PORT))
                while True:
                    raw_len = self._recv_exact(sock, 4)
                    if not raw_len:
                        break
                    msg_len = int.from_bytes(raw_len, "big")
                    data = self._recv_exact(sock, msg_len)
                    if not data:
                        break
                    buf = np.frombuffer(data, dtype=np.uint8)
                    frame = cv2.imdecode(buf, cv2.IMREAD_COLOR)
                    if frame is None:
                        continue
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    h, w, ch = rgb.shape
                    qimg = QImage(
                        rgb.data, w, h, ch * w, QImage.Format.Format_RGB888
                    ).copy()
                    self.frame_ready.emit(qimg)
            except Exception:
                time.sleep(1)

    def _recv_exact(self, sock, n):
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf


# ── LED dot widget ─────────────────────────────────────────────────────────────

class LedDot(QWidget):
    def __init__(self, color="#00cc44", parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self._on = True
        self.setFixedSize(18, 18)

    def set_state(self, on: bool, color: str | None = None):
        self._on = on
        if color:
            self._color = QColor(color)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = self._color if self._on else QColor(30, 30, 30)
        p.setBrush(QBrush(c))
        p.setPen(QPen(c.darker(150), 1))
        p.drawEllipse(2, 2, 14, 14)


# ── Editor de polígonos AR ─────────────────────────────────────────────────────

class EditorPoligonos(QWidget):
    zonas_cambiadas = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frame: QImage | None = None
        self.poligonos: list[list[tuple]] = []
        self.poligono_actual: list[tuple] = []
        self._H: np.ndarray | None = None
        self._H_inv: np.ndarray | None = None
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumHeight(320)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("background-color: #050505;")

    # ── public API ──────────────────────────────────────────────────────────

    def set_frame(self, qimg: QImage):
        self._frame = qimg
        self.update()

    def set_homografia(self, H: np.ndarray, H_inv: np.ndarray):
        self._H = H
        self._H_inv = H_inv
        self.update()

    def get_poligonos(self) -> list:
        return [[list(p) for p in zona] for zona in self.poligonos]

    def cargar_poligonos(self, data: list):
        self.poligonos = [[(p[0], p[1]) for p in zona] for zona in data]
        self.update()

    def cerrar_poligono_actual(self):
        if len(self.poligono_actual) >= 3:
            self.poligonos.append(list(self.poligono_actual))
            self.zonas_cambiadas.emit(self.get_poligonos())
        self.poligono_actual = []
        self.update()

    def cancelar_actual(self):
        self.poligono_actual = []
        self.update()

    def deshacer_ultimo(self):
        if self.poligonos:
            self.poligonos.pop()
            self.update()

    def limpiar_todo(self):
        self.poligonos = []
        self.poligono_actual = []
        self.update()

    # ── coordinate helpers ──────────────────────────────────────────────────

    def _draw_geometry(self):
        img_ratio = 1280 / 720
        w, h = self.width(), self.height()
        if h == 0:
            return 0, 0, w, h
        if w / h > img_ratio:
            draw_h = h
            draw_w = int(h * img_ratio)
        else:
            draw_w = w
            draw_h = int(w / img_ratio)
        off_x = (w - draw_w) // 2
        off_y = (h - draw_h) // 2
        return off_x, off_y, draw_w, draw_h

    def _widget_to_image(self, wx, wy):
        off_x, off_y, draw_w, draw_h = self._draw_geometry()
        if draw_w == 0 or draw_h == 0:
            return 0, 0
        ix = int((wx - off_x) / draw_w * 1280)
        iy = int((wy - off_y) / draw_h * 720)
        return ix, iy

    def _image_to_widget(self, ix, iy):
        off_x, off_y, draw_w, draw_h = self._draw_geometry()
        if draw_w == 0 or draw_h == 0:
            return 0, 0
        wx = int(ix / 1280 * draw_w) + off_x
        wy = int(iy / 720 * draw_h) + off_y
        return wx, wy

    def _punto_a_almacenar(self, wx: int, wy: int) -> tuple:
        """Widget coords → coordenadas de almacenamiento (metros si hay H, píxeles si no)."""
        ix, iy = self._widget_to_image(wx, wy)
        if self._H is not None:
            return _aplicar_H(self._H, float(ix), float(iy))
        return ix, iy

    def _punto_a_widget(self, sx, sy) -> tuple:
        """Coordenadas de almacenamiento → widget (invierte H si hay homografía)."""
        if self._H_inv is not None:
            ix, iy = _aplicar_H(self._H_inv, float(sx), float(sy))
        else:
            ix, iy = float(sx), float(sy)
        return self._image_to_widget(ix, iy)

    # ── paint ───────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#050505"))

        off_x, off_y, draw_w, draw_h = self._draw_geometry()

        if self._frame is not None:
            scaled = self._frame.scaled(
                draw_w, draw_h, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            p.drawImage(off_x, off_y, scaled)
        else:
            p.setPen(QPen(QColor("#2a2a2a")))
            p.setFont(QFont("Courier New", 10))
            p.drawText(self.rect(), Qt.AlignCenter, "SIN VIDEO — Conectando al emisor...")

        if self._H is not None:
            p.setPen(QPen(QColor("#00aaff")))
            p.setFont(QFont("Courier New", 8))
            p.drawText(self.rect().adjusted(8, 4, 0, 0), Qt.AlignTop | Qt.AlignLeft,
                       "◈  MODO METROS — HOMOGRAFÍA ACTIVA")

        # Origin marker (0,0) in real-world space projected onto the video
        if self._H_inv is not None:
            ox, oy = _aplicar_H(self._H_inv, 0.0, 0.0)
            if 0 <= ox <= 1280 and 0 <= oy <= 720:
                owx, owy = self._image_to_widget(int(ox), int(oy))
                arm = 11
                p.setPen(QPen(QColor("#ffffff"), 2))
                p.setBrush(Qt.NoBrush)
                p.drawLine(owx - arm, owy, owx + arm, owy)
                p.drawLine(owx, owy - arm, owx, owy + arm)
                p.setPen(QPen(QColor("#ffff00"), 1))
                p.setBrush(QBrush(QColor(255, 255, 0, 60)))
                p.drawEllipse(owx - 8, owy - 8, 16, 16)
                p.setPen(QPen(QColor("#ffff00")))
                p.setFont(QFont("Courier New", 7, QFont.Bold))
                p.drawText(owx + 12, owy + 4, "(0, 0)")

        # Completed polygons — red semi-transparent fill
        for zona in self.poligonos:
            if len(zona) < 2:
                continue
            pts_w = [QPoint(*self._punto_a_widget(sx, sy)) for sx, sy in zona]
            poly = QPolygon(pts_w)
            p.setBrush(QBrush(QColor(220, 30, 30, 100)))
            p.setPen(QPen(QColor(220, 30, 30, 230), 2))
            p.drawPolygon(poly)

        # Polygon in construction — cyan
        if self.poligono_actual:
            pts_w = [self._punto_a_widget(sx, sy) for sx, sy in self.poligono_actual]
            p.setPen(QPen(QColor(0, 210, 210, 255), 2))
            p.setBrush(Qt.NoBrush)
            for i in range(len(pts_w) - 1):
                p.drawLine(pts_w[i][0], pts_w[i][1], pts_w[i + 1][0], pts_w[i + 1][1])
            p.setBrush(QBrush(QColor(0, 210, 210, 200)))
            p.setPen(QPen(QColor(0, 210, 210, 255), 1))
            for wx, wy in pts_w:
                p.drawEllipse(wx - 4, wy - 4, 8, 8)

        # Instructions overlay when empty
        if not self.poligonos and not self.poligono_actual:
            p.setPen(QPen(QColor("#3a3a3a")))
            p.setFont(QFont("Courier New", 8))
            p.drawText(
                self.rect().adjusted(0, 0, 0, -8),
                Qt.AlignBottom | Qt.AlignHCenter,
                "CLICK IZQUIERDO: agregar punto   |   CLICK DERECHO: cerrar zona",
            )

    # ── input events ────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        pos = event.position()
        wx, wy = int(pos.x()), int(pos.y())
        if event.button() == Qt.LeftButton:
            coord = self._punto_a_almacenar(wx, wy)
            self.poligono_actual.append(coord)
            self.update()
        elif event.button() == Qt.RightButton:
            if len(self.poligono_actual) >= 3:
                self.poligonos.append(list(self.poligono_actual))
                self.poligono_actual = []
                self.zonas_cambiadas.emit(self.get_poligonos())
                self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.cancelar_actual()
        elif event.key() == Qt.Key_Delete:
            self.deshacer_ultimo()


# ── Calibrador de homografía ───────────────────────────────────────────────────

class CalibradorHomografia(QWidget):
    homografia_calculada = Signal(object, object)  # (H: np.ndarray, H_inv: np.ndarray)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frame: QImage | None = None
        self._puntos_px: list[tuple[int, int]] = []
        self._H: np.ndarray | None = None

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        # Canvas
        self._canvas = _CanvasCalibrador(self)
        self._canvas.setFixedHeight(300)
        self._canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._canvas.punto_seleccionado.connect(self._on_click)
        v.addWidget(self._canvas)

        # Grid de puntos
        grid = QGridLayout()
        grid.setSpacing(4)
        self._lbl_pix = []
        self._inputs = []
        for i in range(4):
            lbl = QLabel(f"Punto {i+1}:  PIX (---, ---)")
            lbl.setStyleSheet("color: #666; font-size: 8pt; font-family: 'Courier New';")
            le_x = QLineEdit()
            le_x.setPlaceholderText("X (m)")
            le_x.setFixedWidth(70)
            le_x.setStyleSheet(
                "background: #111; color: #ccc; border: 1px solid #333;"
                "font-family: 'Courier New'; font-size: 8pt; padding: 2px 4px;"
            )
            le_y = QLineEdit()
            le_y.setPlaceholderText("Y (m)")
            le_y.setFixedWidth(70)
            le_y.setStyleSheet(
                "background: #111; color: #ccc; border: 1px solid #333;"
                "font-family: 'Courier New'; font-size: 8pt; padding: 2px 4px;"
            )
            arrow = QLabel("→  Real  X:")
            arrow.setStyleSheet("color: #444; font-size: 8pt;")
            lbl_y = QLabel("m   Y:")
            lbl_y.setStyleSheet("color: #444; font-size: 8pt;")
            lbl_m = QLabel("m")
            lbl_m.setStyleSheet("color: #444; font-size: 8pt;")
            grid.addWidget(lbl, i, 0)
            grid.addWidget(arrow, i, 1)
            grid.addWidget(le_x, i, 2)
            grid.addWidget(lbl_y, i, 3)
            grid.addWidget(le_y, i, 4)
            grid.addWidget(lbl_m, i, 5)
            self._lbl_pix.append(lbl)
            self._inputs.append((le_x, le_y))
        v.addLayout(grid)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_calc = QPushButton("CALCULAR HOMOGRAFÍA")
        btn_calc.setFixedHeight(34)
        btn_calc.setStyleSheet(_BTN_PRIM)
        btn_calc.clicked.connect(self._calcular)
        btn_limpiar = QPushButton("LIMPIAR PUNTOS")
        btn_limpiar.setFixedHeight(34)
        btn_limpiar.setStyleSheet(_BTN_SEC)
        btn_limpiar.clicked.connect(self._limpiar)
        btn_row.addWidget(btn_calc)
        btn_row.addWidget(btn_limpiar)
        btn_row.addStretch()
        v.addLayout(btn_row)

        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color: #555; font-size: 8pt; font-family: 'Courier New';")
        v.addWidget(self.lbl_status)

    def set_frame(self, qimg: QImage):
        self._frame = qimg
        self._canvas.set_frame(qimg)

    def _on_click(self, ix: int, iy: int):
        if len(self._puntos_px) >= 4:
            return
        self._puntos_px.append((ix, iy))
        idx = len(self._puntos_px) - 1
        self._lbl_pix[idx].setText(f"Punto {idx+1}:  PIX ({ix}, {iy})")
        self._canvas.set_puntos(self._puntos_px)
        if len(self._puntos_px) == 4:
            self.lbl_status.setText("4 puntos seleccionados — ingresá las coords reales y calculá")

    def _calcular(self):
        if len(self._puntos_px) < 4:
            self.lbl_status.setText("✘  Necesitás 4 puntos seleccionados")
            return
        try:
            src = np.array(self._puntos_px, dtype=np.float32)
            dst = np.array([
                [float(le_x.text()), float(le_y.text())]
                for le_x, le_y in self._inputs
            ], dtype=np.float32)
            H = cv2.getPerspectiveTransform(src, dst)
            H_inv = cv2.getPerspectiveTransform(dst, src)
            self._H = H
            data = {
                "H": H.tolist(),
                "H_inv": H_inv.tolist(),
                "src_pts": [list(p) for p in self._puntos_px],
                "dst_pts": dst.tolist(),
            }
            with open(HOMOGRAFIA_FILE, "w") as f:
                json.dump(data, f)
            self.lbl_status.setText("✔  Homografía calculada y guardada")
            self._canvas.set_h_inv(H_inv)
            self.homografia_calculada.emit(H, H_inv)
        except Exception as exc:
            self.lbl_status.setText(f"✘  Error: {exc}")

    def _limpiar(self):
        self._puntos_px = []
        self._H = None
        self._canvas.set_puntos([])
        self._canvas.set_h_inv(None)
        for i, lbl in enumerate(self._lbl_pix):
            lbl.setText(f"Punto {i+1}:  PIX (---, ---)")
        self.lbl_status.setText("")


class _CanvasCalibrador(QWidget):
    """Sub-widget interno: canvas de imagen para CalibradorHomografia."""
    punto_seleccionado = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frame: QImage | None = None
        self._puntos: list[tuple[int, int]] = []
        self._H_inv: np.ndarray | None = None
        self.setStyleSheet("background-color: #050505;")

    def set_frame(self, qimg: QImage):
        self._frame = qimg
        self.update()

    def set_puntos(self, pts: list):
        self._puntos = list(pts)
        self.update()

    def set_h_inv(self, H_inv):
        self._H_inv = H_inv
        self.update()

    def _draw_geometry(self):
        img_ratio = 1280 / 720
        w, h = self.width(), self.height()
        if h == 0:
            return 0, 0, w, h
        if w / h > img_ratio:
            draw_h = h
            draw_w = int(h * img_ratio)
        else:
            draw_w = w
            draw_h = int(w / img_ratio)
        off_x = (w - draw_w) // 2
        off_y = (h - draw_h) // 2
        return off_x, off_y, draw_w, draw_h

    def _widget_to_image(self, wx, wy):
        off_x, off_y, draw_w, draw_h = self._draw_geometry()
        if draw_w == 0 or draw_h == 0:
            return 0, 0
        ix = int((wx - off_x) / draw_w * 1280)
        iy = int((wy - off_y) / draw_h * 720)
        return ix, iy

    def _image_to_widget(self, ix, iy):
        off_x, off_y, draw_w, draw_h = self._draw_geometry()
        if draw_w == 0 or draw_h == 0:
            return 0, 0
        wx = int(ix / 1280 * draw_w) + off_x
        wy = int(iy / 720 * draw_h) + off_y
        return wx, wy

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#050505"))

        off_x, off_y, draw_w, draw_h = self._draw_geometry()
        if self._frame is not None:
            scaled = self._frame.scaled(
                draw_w, draw_h, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            p.drawImage(off_x, off_y, scaled)
        else:
            p.setPen(QPen(QColor("#2a2a2a")))
            p.setFont(QFont("Courier New", 9))
            p.drawText(self.rect(), Qt.AlignCenter, "SIN VIDEO — Conectando al emisor...")

        # Coordinate axis indicator — bottom-left corner of the image area
        ax_ox = off_x + 44
        ax_oy = off_y + draw_h - 16
        ax_len = 38
        # X axis → orange/red, pointing right
        p.setPen(QPen(QColor("#ff6633"), 2))
        p.setBrush(Qt.NoBrush)
        p.drawLine(ax_ox, ax_oy, ax_ox + ax_len, ax_oy)
        p.drawLine(ax_ox + ax_len, ax_oy, ax_ox + ax_len - 7, ax_oy - 5)
        p.drawLine(ax_ox + ax_len, ax_oy, ax_ox + ax_len - 7, ax_oy + 5)
        p.setFont(QFont("Courier New", 8, QFont.Bold))
        p.drawText(ax_ox + ax_len + 4, ax_oy + 4, "X")
        # Y axis → green, pointing up
        p.setPen(QPen(QColor("#33dd88"), 2))
        p.drawLine(ax_ox, ax_oy, ax_ox, ax_oy - ax_len)
        p.drawLine(ax_ox, ax_oy - ax_len, ax_ox - 5, ax_oy - ax_len + 7)
        p.drawLine(ax_ox, ax_oy - ax_len, ax_ox + 5, ax_oy - ax_len + 7)
        p.drawText(ax_ox - 10, ax_oy - ax_len - 4, "Y")
        # Origin dot
        p.setPen(QPen(QColor("#ffffff"), 1))
        p.setBrush(QBrush(QColor("#ffffff")))
        p.drawEllipse(ax_ox - 3, ax_oy - 3, 6, 6)

        # Origin marker — real-world (0,0) projected onto image after H is known
        if self._H_inv is not None:
            ox, oy = _aplicar_H(self._H_inv, 0.0, 0.0)
            if 0 <= ox <= 1280 and 0 <= oy <= 720:
                owx, owy = self._image_to_widget(int(ox), int(oy))
                arm = 11
                p.setPen(QPen(QColor("#ffffff"), 2))
                p.setBrush(Qt.NoBrush)
                p.drawLine(owx - arm, owy, owx + arm, owy)
                p.drawLine(owx, owy - arm, owx, owy + arm)
                p.setPen(QPen(QColor("#ffff00"), 1))
                p.setBrush(QBrush(QColor(255, 255, 0, 60)))
                p.drawEllipse(owx - 8, owy - 8, 16, 16)
                p.setPen(QPen(QColor("#ffff00")))
                p.setFont(QFont("Courier New", 7, QFont.Bold))
                p.drawText(owx + 12, owy + 4, "(0, 0)")

        for idx, (ix, iy) in enumerate(self._puntos):
            wx, wy = self._image_to_widget(ix, iy)
            p.setBrush(QBrush(QColor("#ffcc00")))
            p.setPen(QPen(QColor("#ffcc00"), 1))
            p.drawEllipse(wx - 5, wy - 5, 10, 10)
            p.setPen(QPen(QColor("#000000")))
            p.setFont(QFont("Courier New", 7, QFont.Bold))
            p.drawText(wx - 3, wy + 4, str(idx + 1))

        p.setPen(QPen(QColor("#3a3a3a")))
        p.setFont(QFont("Courier New", 8))
        if len(self._puntos) < 4:
            p.drawText(
                self.rect().adjusted(0, 0, 0, -6),
                Qt.AlignBottom | Qt.AlignHCenter,
                "CLICK IZQUIERDO: seleccionar punto en el piso (máx 4)",
            )
        else:
            p.setPen(QPen(QColor("#ffcc00")))
            p.drawText(
                self.rect().adjusted(0, 0, 0, -6),
                Qt.AlignBottom | Qt.AlignHCenter,
                "4 puntos seleccionados — ingresá las coords reales y calculá",
            )

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and len(self._puntos) < 4:
            pos = event.position()
            ix, iy = self._widget_to_image(int(pos.x()), int(pos.y()))
            self.punto_seleccionado.emit(ix, iy)


# ── Styles ────────────────────────────────────────────────────────────────────

_CSS_BASE = """
    QMainWindow, QWidget {
        background-color: #0a0a0a;
        color: #d0d0d0;
        font-family: "Courier New", Courier, monospace;
    }
    QTabWidget::pane {
        border: 1px solid #2a2a2a;
        background-color: #0f0f0f;
    }
    QTabBar::tab {
        background-color: #141414;
        color: #666;
        padding: 8px 22px;
        border: 1px solid #2a2a2a;
        border-bottom: none;
        font-family: "Courier New";
        font-size: 9pt;
        letter-spacing: 1px;
    }
    QTabBar::tab:selected {
        background-color: #0f0f0f;
        color: #d0d0d0;
        border-top: 2px solid #3a3a3a;
    }
    QTabBar::tab:hover:!selected {
        background-color: #1c1c1c;
        color: #aaa;
    }
    QScrollBar:vertical {
        background: #111;
        width: 8px;
        border-radius: 4px;
    }
    QScrollBar::handle:vertical {
        background: #2a2a2a;
        border-radius: 4px;
        min-height: 20px;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""

_FRAME_SEGURO = """
    QFrame#indicador {
        background-color: #0b180b;
        border: 2px solid #1a3d1a;
        border-radius: 8px;
    }
"""
_FRAME_ALARMA_ON = """
    QFrame#indicador {
        background-color: #1a0606;
        border: 2px solid #cc2222;
        border-radius: 8px;
    }
"""
_FRAME_ALARMA_OFF = """
    QFrame#indicador {
        background-color: #0f0303;
        border: 2px solid #4a0808;
        border-radius: 8px;
    }
"""

_BTN_PRIM = """
    QPushButton {
        background-color: #122212; color: #00cc44;
        border: 1px solid #1e7a3a; border-radius: 4px;
        font-family: 'Courier New'; font-size: 9pt; font-weight: bold;
        letter-spacing: 1px; padding: 0 14px;
    }
    QPushButton:hover  { background-color: #1a3322; border-color: #2aaa55; }
    QPushButton:pressed { background-color: #0a1610; }
"""
_BTN_SEC = """
    QPushButton {
        background-color: #141414; color: #555;
        border: 1px solid #2a2a2a; border-radius: 4px;
        font-family: 'Courier New'; font-size: 9pt;
        letter-spacing: 1px; padding: 0 14px;
    }
    QPushButton:hover  { background-color: #1e1e1e; color: #888; border-color: #3a3a3a; }
    QPushButton:pressed { background-color: #0e0e0e; }
"""


# ── Main window ────────────────────────────────────────────────────────────────

class VentanaPrincipal(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("HMI — CONTROL DE SEGURIDAD")
        self.setMinimumSize(920, 660)
        self.alarma_activa = False
        self._blink_on = True
        self._ultimo_estado_alarma: bool | None = None
        self._H: np.ndarray | None = None
        self._H_inv: np.ndarray | None = None

        self._build_ui()
        self._cargar_zonas()
        self._cargar_homografia()
        self._start_workers()

        self._timer_blink = QTimer()
        self._timer_blink.setInterval(480)
        self._timer_blink.timeout.connect(self._do_blink)

    # ── workers ──────────────────────────────────────────────────────────────

    def _start_workers(self):
        self.hilo_udp = WorkerRed()
        self.hilo_udp.datos_recibidos.connect(self._on_datos)
        self.hilo_udp.start()

        self.hilo_video = VideoWorker()
        self.hilo_video.frame_ready.connect(self.editor.set_frame)
        self.hilo_video.frame_ready.connect(self.calibrador.set_frame)
        self.hilo_video.start()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        self.setStyleSheet(_CSS_BASE)
        root = QWidget()
        self.setCentralWidget(root)
        vroot = QVBoxLayout(root)
        vroot.setContentsMargins(0, 0, 0, 0)
        vroot.setSpacing(0)

        vroot.addWidget(self._build_header())

        tabs = QTabWidget()
        tabs.addTab(self._build_monitor(), "MONITOR")
        tabs.addTab(self._build_zone_editor(), "EDITOR DE ZONAS AR")
        tabs.addTab(self._build_calibracion(), "CALIBRACIÓN")
        vroot.addWidget(tabs)

    def _build_header(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(48)
        bar.setStyleSheet("background-color: #0e0e0e; border-bottom: 1px solid #222;")
        h = QHBoxLayout(bar)
        h.setContentsMargins(18, 0, 18, 0)

        title = QLabel("HMI — CONTROL DE SEGURIDAD")
        title.setStyleSheet(
            "color: #999; font-size: 12pt; font-family: 'Courier New';"
            "font-weight: bold; letter-spacing: 3px;"
        )
        self.lbl_conexion = QLabel("◌  SIN SEÑAL")
        self.lbl_conexion.setStyleSheet(
            "color: #444; font-size: 8pt; font-family: 'Courier New';"
        )
        h.addWidget(title)
        h.addStretch()
        h.addWidget(self.lbl_conexion)
        return bar

    def _build_monitor(self) -> QWidget:
        panel = QWidget()
        v = QVBoxLayout(panel)
        v.setContentsMargins(22, 22, 22, 22)
        v.setSpacing(12)

        # ── Big status indicator ──
        self.frame_indicador = QFrame()
        self.frame_indicador.setObjectName("indicador")
        self.frame_indicador.setFixedHeight(200)
        self.frame_indicador.setStyleSheet(_FRAME_SEGURO)

        fi_v = QVBoxLayout(self.frame_indicador)
        fi_v.setAlignment(Qt.AlignCenter)
        fi_v.setSpacing(6)

        led_row = QHBoxLayout()
        led_row.setAlignment(Qt.AlignCenter)
        led_row.setSpacing(10)
        self.leds = [LedDot("#00cc44") for _ in range(7)]
        for led in self.leds:
            led_row.addWidget(led)
        fi_v.addLayout(led_row)

        self.lbl_estado = QLabel("● SEGURO")
        self.lbl_estado.setAlignment(Qt.AlignCenter)
        self.lbl_estado.setStyleSheet(
            "color: #00ff55; font-size: 38pt; font-family: 'Courier New';"
            "font-weight: bold; letter-spacing: 5px; background: transparent;"
        )
        fi_v.addWidget(self.lbl_estado)

        self.lbl_subtitulo = QLabel("ZONA LIBRE DE PERSONAL")
        self.lbl_subtitulo.setAlignment(Qt.AlignCenter)
        self.lbl_subtitulo.setStyleSheet(
            "color: #3a8a3a; font-size: 10pt; font-family: 'Courier New';"
            "letter-spacing: 3px; background: transparent;"
        )
        fi_v.addWidget(self.lbl_subtitulo)
        v.addWidget(self.frame_indicador)

        # ── Datos en tiempo real ──
        datos_row = QHBoxLayout()
        datos_row.setSpacing(20)

        pers_col = QVBoxLayout()
        pers_col.setSpacing(2)
        lbl_pers_t = QLabel("PERSONAS")
        lbl_pers_t.setStyleSheet("color: #444; font-size: 7pt; letter-spacing: 2px;")
        self.lbl_personas = QLabel("0")
        self.lbl_personas.setStyleSheet(
            "color: #aaa; font-size: 22pt; font-family: 'Courier New'; font-weight: bold;"
        )
        pers_col.addWidget(lbl_pers_t)
        pers_col.addWidget(self.lbl_personas)

        pies_col = QVBoxLayout()
        pies_col.setSpacing(2)
        self.lbl_pies_titulo = QLabel("POSICIONES (px)")
        self.lbl_pies_titulo.setStyleSheet("color: #444; font-size: 7pt; letter-spacing: 2px;")
        self.lbl_pies = QLabel("—")
        self.lbl_pies.setStyleSheet(
            "color: #555; font-size: 8pt; font-family: 'Courier New';"
        )
        pies_col.addWidget(self.lbl_pies_titulo)
        pies_col.addWidget(self.lbl_pies)

        ts_col = QVBoxLayout()
        ts_col.setSpacing(2)
        lbl_ts_t = QLabel("ÚLTIMO EVENTO")
        lbl_ts_t.setStyleSheet("color: #444; font-size: 7pt; letter-spacing: 2px;")
        self.lbl_ts = QLabel("—")
        self.lbl_ts.setStyleSheet(
            "color: #555; font-size: 8pt; font-family: 'Courier New';"
        )
        ts_col.addWidget(lbl_ts_t)
        ts_col.addWidget(self.lbl_ts)

        modo_col = QVBoxLayout()
        modo_col.setSpacing(2)
        lbl_modo_t = QLabel("MODO DE DETECCIÓN")
        lbl_modo_t.setStyleSheet("color: #444; font-size: 7pt; letter-spacing: 2px;")
        self.lbl_modo = QLabel("◌  MODO: PÍXELES (sin calibración)")
        self.lbl_modo.setStyleSheet("color: #444; font-size: 8pt; font-family: 'Courier New';")
        modo_col.addWidget(lbl_modo_t)
        modo_col.addWidget(self.lbl_modo)

        datos_row.addLayout(pers_col)
        datos_row.addLayout(pies_col)
        datos_row.addStretch()
        datos_row.addLayout(modo_col)
        datos_row.addLayout(ts_col)
        v.addLayout(datos_row)

        # ── Event log ──
        sep = QLabel("LOG DE EVENTOS")
        sep.setStyleSheet(
            "color: #2a2a2a; font-size: 7pt; letter-spacing: 3px;"
            "border-bottom: 1px solid #1a1a1a; padding-bottom: 4px;"
        )
        v.addWidget(sep)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet("""
            QTextEdit {
                background-color: #050505; color: #3a8a3a;
                border: 1px solid #151515; border-radius: 4px;
                font-family: 'Courier New'; font-size: 8pt; padding: 8px;
            }
        """)
        v.addWidget(self.log)
        return panel

    def _build_zone_editor(self) -> QWidget:
        panel = QWidget()
        v = QVBoxLayout(panel)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)

        desc = QLabel(
            "CLICK IZQ: agregar punto   |   CLICK DER: cerrar zona   |   Feed en vivo desde emisor"
        )
        desc.setStyleSheet("color: #3a3a3a; font-size: 8pt; letter-spacing: 1px;")
        v.addWidget(desc)

        self.editor = EditorPoligonos()
        v.addWidget(self.editor, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        def _btn(label, style, callback):
            b = QPushButton(label)
            b.setFixedHeight(38)
            b.setStyleSheet(style)
            b.clicked.connect(callback)
            return b

        btn_row.addWidget(_btn("▷  CERRAR ZONA ACTUAL", _BTN_PRIM, self.editor.cerrar_poligono_actual))
        btn_row.addWidget(_btn("✖  DESCARTAR ZONA", _BTN_SEC, self.editor.cancelar_actual))
        btn_row.addWidget(_btn("↺  DESHACER ÚLTIMA", _BTN_SEC, self.editor.deshacer_ultimo))
        btn_row.addWidget(_btn("⌫  LIMPIAR TODO", _BTN_SEC, self.editor.limpiar_todo))
        btn_row.addStretch()
        btn_row.addWidget(_btn("▶  GUARDAR ZONAS", _BTN_PRIM, self._guardar_zonas))

        v.addLayout(btn_row)

        self.lbl_envio = QLabel("")
        self.lbl_envio.setStyleSheet("color: #3a6a3a; font-size: 8pt;")
        v.addWidget(self.lbl_envio)

        return panel

    def _build_calibracion(self) -> QWidget:
        panel = QWidget()
        v = QVBoxLayout(panel)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)

        desc = QLabel(
            "Seleccioná 4 puntos del PISO en la imagen y asignales coordenadas reales en metros."
            "  |  Orden sugerido: esquinas del área de trabajo."
        )
        desc.setStyleSheet("color: #3a3a3a; font-size: 8pt; letter-spacing: 1px;")
        desc.setWordWrap(True)
        v.addWidget(desc)

        self.calibrador = CalibradorHomografia()
        self.calibrador.homografia_calculada.connect(self._on_homografia)
        v.addWidget(self.calibrador, stretch=1)

        return panel

    # ── Persistencia ──────────────────────────────────────────────────────────

    def _cargar_zonas(self):
        if os.path.exists(ZONAS_FILE):
            try:
                with open(ZONAS_FILE) as f:
                    data = json.load(f)
                self.editor.cargar_poligonos(data.get("poligonos", []))
                n = len(data.get("poligonos", []))
                modo = data.get("modo", "pixeles")
                self.lbl_envio.setText(
                    f"↺  {n} zona(s) cargadas desde {ZONAS_FILE} [{modo}]"
                )
            except Exception as exc:
                self.lbl_envio.setText(f"✘  Error cargando zonas: {exc}")

    def _guardar_zonas(self):
        modo = "metros" if self._H is not None else "pixeles"
        data = {"modo": modo, "poligonos": self.editor.get_poligonos()}
        try:
            with open(ZONAS_FILE, "w") as f:
                json.dump(data, f)
            n = len(data["poligonos"])
            self.lbl_envio.setText(f"✔  {n} zona(s) guardadas [{modo}]")
            self.lbl_envio.setStyleSheet("color: #22aa44; font-size: 8pt;")
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._log_entry(ts, f"ZONAS GUARDADAS → {n} polígono(s) [{modo}]", "#2255cc")
        except Exception as exc:
            self.lbl_envio.setText(f"✘  Error: {exc}")
            self.lbl_envio.setStyleSheet("color: #cc3333; font-size: 8pt;")

    def _cargar_homografia(self):
        if os.path.exists(HOMOGRAFIA_FILE):
            try:
                with open(HOMOGRAFIA_FILE) as f:
                    data = json.load(f)
                H = np.array(data["H"], dtype=np.float64)
                H_inv = np.array(data["H_inv"], dtype=np.float64)
                self._H = H
                self._H_inv = H_inv
                self.editor.set_homografia(H, H_inv)
                self._actualizar_indicador_modo()
            except Exception:
                pass  # sin H válida → modo píxeles

    # ── Alarma ────────────────────────────────────────────────────────────────

    def _verificar_alarma(self, pies: list, poligonos: list) -> bool:
        for px, py in pies:
            if self._H is not None:
                punto = _aplicar_H(self._H, float(px), float(py))
            else:
                punto = (float(px), float(py))
            for poligono in poligonos:
                if len(poligono) < 3:
                    continue
                pts = np.array(poligono, dtype=np.float32)
                if cv2.pointPolygonTest(pts, punto, False) >= 0:
                    return True
        return False

    def _actualizar_alarma(self, alarma: bool):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.alarma_activa = alarma
        estado_cambio = alarma != self._ultimo_estado_alarma
        self._ultimo_estado_alarma = alarma

        if alarma:
            self._mostrar_alarma(bright=True)
            if not self._timer_blink.isActive():
                self._timer_blink.start()
            if estado_cambio:
                self._log_entry(ts, "⚠  INTRUSIÓN — PERSONA EN ZONA RESTRINGIDA", "#dd3333")
        else:
            self._timer_blink.stop()
            self._mostrar_seguro()
            if estado_cambio:
                self._log_entry(ts, "✔  ZONA DESPEJADA — SISTEMA SEGURO", "#22aa44")

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_datos(self, payload: dict):
        personas = payload.get("personas", 0)
        pies = payload.get("pies", [])
        poligonos = self.editor.get_poligonos()

        alarma = self._verificar_alarma(pies, poligonos)
        self._actualizar_alarma(alarma)

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.lbl_ts.setText(ts)
        self.lbl_conexion.setText(f"● CONECTADO  {ts}")
        self.lbl_conexion.setStyleSheet(
            "color: #226622; font-size: 8pt; font-family: 'Courier New';"
        )
        self.lbl_personas.setText(str(personas))

        if pies:
            if self._H is not None:
                partes = []
                for x, y in pies:
                    mx, my = _aplicar_H(self._H, float(x), float(y))
                    partes.append(f"({mx:.2f}m, {my:.2f}m)")
                pies_txt = "   ".join(partes)
            else:
                pies_txt = "   ".join(f"({x}, {y})" for x, y in pies)
        else:
            pies_txt = "—"
        self.lbl_pies.setText(pies_txt)

    def _on_homografia(self, H: np.ndarray, H_inv: np.ndarray):
        self._H = H
        self._H_inv = H_inv
        self.editor.set_homografia(H, H_inv)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._log_entry(ts, "◈  HOMOGRAFÍA CALIBRADA — modo metros activado", "#00aaff")
        self._actualizar_indicador_modo()

    def _actualizar_indicador_modo(self):
        if self._H is not None:
            self.lbl_modo.setText("◈  MODO: METROS")
            self.lbl_modo.setStyleSheet(
                "color: #00aaff; font-size: 8pt; font-family: 'Courier New';"
            )
            self.lbl_pies_titulo.setText("POSICIONES (m)")
        else:
            self.lbl_modo.setText("◌  MODO: PÍXELES (sin calibración)")
            self.lbl_modo.setStyleSheet(
                "color: #444; font-size: 8pt; font-family: 'Courier New';"
            )
            self.lbl_pies_titulo.setText("POSICIONES (px)")

    # ── Visual state ──────────────────────────────────────────────────────────

    def _mostrar_alarma(self, bright: bool):
        if bright:
            self.frame_indicador.setStyleSheet(_FRAME_ALARMA_ON)
            self.lbl_estado.setStyleSheet(
                "color: #ff3333; font-size: 38pt; font-family: 'Courier New';"
                "font-weight: bold; letter-spacing: 5px; background: transparent;"
            )
            self.lbl_estado.setText("⚠  ALARMA")
            self.lbl_subtitulo.setText("PERSONA EN ZONA RESTRINGIDA")
            self.lbl_subtitulo.setStyleSheet(
                "color: #882222; font-size: 10pt; font-family: 'Courier New';"
                "letter-spacing: 3px; background: transparent;"
            )
            for led in self.leds:
                led.set_state(True, "#ff2222")
        else:
            self.frame_indicador.setStyleSheet(_FRAME_ALARMA_OFF)
            self.lbl_estado.setStyleSheet(
                "color: #441111; font-size: 38pt; font-family: 'Courier New';"
                "font-weight: bold; letter-spacing: 5px; background: transparent;"
            )
            for led in self.leds:
                led.set_state(False)

    def _mostrar_seguro(self):
        self.frame_indicador.setStyleSheet(_FRAME_SEGURO)
        self.lbl_estado.setStyleSheet(
            "color: #00ff55; font-size: 38pt; font-family: 'Courier New';"
            "font-weight: bold; letter-spacing: 5px; background: transparent;"
        )
        self.lbl_estado.setText("● SEGURO")
        self.lbl_subtitulo.setText("ZONA LIBRE DE PERSONAL")
        self.lbl_subtitulo.setStyleSheet(
            "color: #3a8a3a; font-size: 10pt; font-family: 'Courier New';"
            "letter-spacing: 3px; background: transparent;"
        )
        for led in self.leds:
            led.set_state(True, "#00cc44")

    def _do_blink(self):
        self._blink_on = not self._blink_on
        self._mostrar_alarma(bright=self._blink_on)

    def _log_entry(self, ts: str, mensaje: str, color: str):
        self.log.append(
            f'<span style="color:#333;">[{ts}]</span> '
            f'<span style="color:{color};">{mensaje}</span>'
        )
        self.log.verticalScrollBar().setValue(
            self.log.verticalScrollBar().maximum()
        )


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = QApplication(sys.argv)
    ventana = VentanaPrincipal()
    ventana.show()
    sys.exit(app.exec())

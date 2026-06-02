import sys
import json
import socket
import threading
import queue
import struct
import cv2
import torch
import numpy as np
from ultralytics import YOLO

from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel,
    QVBoxLayout, QHBoxLayout, QFrame, QSizePolicy,
)

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
HMI_IP     = "172.23.190.14"
UDP_PORT   = 5005
TCP_PORT   = 5010
#CAMERA_URL  = "http://192.168.100.178:4747/video"
CAMERA_URL = "http://172.23.199.84:4747/video"
WIDTH  = 1280
HEIGHT = 720


# ---------------------------------------------------------------------------
# Servidor TCP de video — puerto 5010
# ---------------------------------------------------------------------------
class VideoStreamServer(threading.Thread):
    def __init__(self, frame_queue: queue.Queue):
        super().__init__(daemon=True)
        self.frame_queue = frame_queue

    def run(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", TCP_PORT))
        srv.listen(1)
        while True:
            conn, addr = srv.accept()
            print(f"[TCP] Receptor conectado: {addr}")
            try:
                while True:
                    frame = self.frame_queue.get()
                    _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    data = buf.tobytes()
                    conn.sendall(len(data).to_bytes(4, 'big') + data)
            except Exception:
                conn.close()
                print("[TCP] Receptor desconectado, esperando reconexión...")


# ---------------------------------------------------------------------------
# Hilo de captura + inferencia
# ---------------------------------------------------------------------------
class CameraWorker(QThread):
    frame_ready = Signal(QImage, int, list)  # qimg, count_personas, pies_list

    def __init__(self, frame_queue: queue.Queue):
        super().__init__()
        self._running = True
        self.frame_queue = frame_queue
        self.frame_counter = 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(0.1)
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = YOLO("yolo26n-seg.pt")
        self.cap = cv2.VideoCapture(CAMERA_URL)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)

    def run(self):
        while self._running:
            ret, frame = self.cap.read()
            if not ret:
                continue

            self.frame_counter += 1

            # --- YOLO (sin modificar) ---
            results = self.model(frame, device=self.device, verbose=False, classes=[0])

            overlay = frame.copy()
            count_personas = 0
            pies = []

            if results[0].masks is not None:
                boxes = results[0].boxes
                count_personas = len(boxes)

                for i in range(count_personas):
                    x1, y1, x2, y2 = map(int, boxes.xyxy[i])
                    centro_pies_x = int((x1 + x2) / 2)
                    centro_pies_y = y2

                    pies.append([centro_pies_x, centro_pies_y])

                    np.random.seed(int(boxes.cls[i]))
                    color = np.random.randint(80, 255, 3).tolist()

                    mask = results[0].masks.data.cpu().numpy()[i]
                    mask_resized = cv2.resize(mask, (frame.shape[1], frame.shape[0]))
                    overlay[mask_resized > 0.5] = color
                    cv2.circle(frame, (centro_pies_x, centro_pies_y), 10, color, -1)

            # --- UDP: JSON con datos de detección ---
            payload = {
                "personas": count_personas,
                "pies": pies,
                "frame_id": self.frame_counter,
            }
            try:
                self.sock.sendto(json.dumps(payload).encode(), (HMI_IP, UDP_PORT))
            except Exception as e:
                print(f"Error UDP: {e}")

            # Componer frame final
            frame_final = cv2.addWeighted(overlay, 0.5, frame, 0.5, 0)

            # Encolar para stream TCP (descarta si el receptor no consume a tiempo)
            try:
                self.frame_queue.put_nowait(frame_final)
            except queue.Full:
                pass

            # Convertir a QImage para la UI local
            rgb = cv2.cvtColor(frame_final, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()

            self.frame_ready.emit(qimg, count_personas, pies)

    def stop(self):
        self._running = False
        self.cap.release()
        self.sock.close()
        self.quit()
        self.wait()


# ---------------------------------------------------------------------------
# Estilos
# ---------------------------------------------------------------------------
STYLE_BASE = """
QMainWindow, QWidget#central {
    background-color: #0d0d0d;
}
QWidget#panel {
    background-color: #111111;
    border-left: 2px solid #252525;
}
QLabel#title {
    color: #d0d0d0;
    font-family: "Courier New", Courier, monospace;
    font-size: 15px;
    font-weight: bold;
    letter-spacing: 3px;
    padding: 14px 8px;
    border-bottom: 1px solid #252525;
    background-color: #0a0a0a;
    qproperty-alignment: AlignCenter;
}
QLabel#section_lbl {
    color: #555555;
    font-family: "Courier New", Courier, monospace;
    font-size: 9px;
    letter-spacing: 2px;
    padding: 8px 14px 2px 14px;
}
QLabel#count_val {
    color: #00ff88;
    font-family: "Courier New", Courier, monospace;
    font-size: 42px;
    font-weight: bold;
    padding: 0px 14px 4px 14px;
    qproperty-alignment: AlignCenter;
}
QLabel#pies_val {
    color: #bbbbbb;
    font-family: "Courier New", Courier, monospace;
    font-size: 12px;
    padding: 4px 14px;
    min-height: 80px;
}
QLabel#meta_lbl {
    color: #404040;
    font-family: "Courier New", Courier, monospace;
    font-size: 9px;
    letter-spacing: 1px;
    padding: 2px 14px;
    qproperty-alignment: AlignCenter;
}
QFrame#sep {
    background-color: #222222;
    max-height: 1px;
    margin: 6px 14px;
}
QLabel#video_idle {
    background-color: #050505;
    color: #2a2a2a;
    font-family: "Courier New", Courier, monospace;
    font-size: 13px;
    letter-spacing: 2px;
    qproperty-alignment: AlignCenter;
}
"""

LED_ACTIVE_CSS = """
    background-color: #001a00;
    color: #00ee44;
    font-family: "Courier New", Courier, monospace;
    font-size: 12px;
    font-weight: bold;
    letter-spacing: 2px;
    padding: 10px 14px;
    border: 1px solid #005500;
    border-radius: 3px;
    margin: 4px 14px 8px 14px;
"""

LED_IDLE_CSS = """
    background-color: #1a1a00;
    color: #888800;
    font-family: "Courier New", Courier, monospace;
    font-size: 12px;
    font-weight: bold;
    letter-spacing: 2px;
    padding: 10px 14px;
    border: 1px solid #444400;
    border-radius: 3px;
    margin: 4px 14px 8px 14px;
"""


# ---------------------------------------------------------------------------
# Ventana principal
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("EMISOR — DETECCIÓN DE PERSONAS")
        self.setStyleSheet(STYLE_BASE)

        self._build_ui()

        self.frame_queue = queue.Queue(maxsize=1)
        self.video_server = VideoStreamServer(self.frame_queue)
        self.video_server.start()

        self.worker = CameraWorker(self.frame_queue)
        self.worker.frame_ready.connect(self._update_ui)
        self.worker.start()

    def _build_ui(self):
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)

        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- Video ----
        self.video_lbl = QLabel("AGUARDANDO SEÑAL DE CÁMARA...")
        self.video_lbl.setObjectName("video_idle")
        self.video_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.video_lbl.setMinimumSize(640, 360)
        root.addWidget(self.video_lbl, stretch=1)

        # ---- Panel lateral ----
        panel = QWidget()
        panel.setObjectName("panel")
        panel.setFixedWidth(270)
        col = QVBoxLayout(panel)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        title = QLabel("EMISOR\nDETECCIÓN DE PERSONAS")
        title.setObjectName("title")
        col.addWidget(title)

        # Contador personas
        col.addWidget(self._section("PERSONAS DETECTADAS"))
        self.lbl_count = QLabel("0")
        self.lbl_count.setObjectName("count_val")
        col.addWidget(self.lbl_count)

        col.addWidget(self._sep())

        # Coordenadas de pies
        col.addWidget(self._section("COORDENADAS DE PIES"))
        self.lbl_pies = QLabel("—")
        self.lbl_pies.setObjectName("pies_val")
        self.lbl_pies.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.lbl_pies.setWordWrap(True)
        col.addWidget(self.lbl_pies)

        col.addWidget(self._sep())

        # LED estado
        col.addWidget(self._section("ESTADO DEL SISTEMA"))
        self.led = QLabel("○  INICIANDO...")
        self.led.setStyleSheet(LED_IDLE_CSS)
        self.led.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(self.led)

        col.addStretch()

        # Metadata
        col.addWidget(self._sep())
        device_str = "CUDA ACTIVO" if torch.cuda.is_available() else "MODO CPU"
        col.addWidget(self._meta(device_str))
        col.addWidget(self._meta(f"UDP  {HMI_IP}:{UDP_PORT}"))
        col.addWidget(self._meta(f"TCP  0.0.0.0:{TCP_PORT}"))
        spacer = QWidget()
        spacer.setFixedHeight(10)
        col.addWidget(spacer)

        root.addWidget(panel)

    def _section(self, text):
        lbl = QLabel(text)
        lbl.setObjectName("section_lbl")
        return lbl

    def _meta(self, text):
        lbl = QLabel(text)
        lbl.setObjectName("meta_lbl")
        return lbl

    def _sep(self):
        f = QFrame()
        f.setObjectName("sep")
        return f

    def _update_ui(self, qimg: QImage, count: int, pies: list):
        # Frame de video
        pix = QPixmap.fromImage(qimg).scaled(
            self.video_lbl.width(),
            self.video_lbl.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.video_lbl.setPixmap(pix)

        # Contador
        self.lbl_count.setText(str(count))
        count_color = "#ff8844" if count > 0 else "#00ff88"
        self.lbl_count.setStyleSheet(
            f"color: {count_color}; font-family: 'Courier New', monospace; "
            f"font-size: 42px; font-weight: bold; padding: 0px 14px 4px 14px;"
        )

        # Coordenadas de pies
        if pies:
            lines = [f"PIE {i+1}:  ({p[0]:4d}, {p[1]:4d})" for i, p in enumerate(pies)]
            self.lbl_pies.setText("\n".join(lines))
        else:
            self.lbl_pies.setText("—")

        # LED — activo mientras fluyen frames
        self.led.setText("●  TRANSMITIENDO")
        self.led.setStyleSheet(LED_ACTIVE_CSS)

    def closeEvent(self, event):
        self.worker.stop()
        event.accept()


# ---------------------------------------------------------------------------
def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

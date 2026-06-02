# AGENTE 1 — Emisor (CodigoEmisor.py)

## Contexto del archivo actual
`CodigoEmisor.py` ya existe y tiene una UI PySide6 funcional. Contiene:
- `CameraWorker` (QThread): captura cámara, corre YOLO, calcula cuadrantes, envía UDP "1"/"0"
- `ConfigWorker` (QThread): escucha UDP puerto 5006 para recibir config de zonas
- `MainWindow`: UI con video + panel lateral (contador, zonas, LED)

## Tu tarea
Modificar `CodigoEmisor.py` para implementar la nueva arquitectura. El archivo resultante
debe llamarse `CodigoEmisor.py` (mismo nombre, reemplazar).

---

## Qué ELIMINAR del código actual

1. **Toda la lógica de cuadrantes y zonas**:
   - La función `obtener_cuadrante(x, y)`
   - La constante `ZONAS_PELIGROSAS = [0, 2, 3, 5, 6, 8]`
   - El dibujado de la cuadrícula 3x3 sobre el frame (los `cv2.line` y `cv2.putText`)
   - El cálculo de `zonas_ocupadas`, `alarma_activa` y `es_peligro` dentro de `CameraWorker`
   - La constante `CONFIG_PORT = 5006`
   - La clase `ConfigWorker` completa (ya no se recibe config del receptor)
   - En `MainWindow`: el widget "ZONAS OCUPADAS", "ZONAS RESTRINGIDAS", y todo lo que
     depende de `ZONAS_PELIGROSAS`
   - La instancia `self.config_worker` en `MainWindow.__init__`

2. **El envío UDP actual** (`trigger_alarma = "1" if alarma_activa else "0"`):
   Reemplazar completamente por el nuevo protocolo JSON (ver abajo).

---

## Qué AGREGAR / MODIFICAR

### 1. Nuevas constantes de configuración

```python
HMI_IP   = "192.168.1.56"   # mantener
UDP_PORT = 5005              # mantener (era HMI_PORT)
TCP_PORT = 5010              # NUEVO: stream de video
CAMERA_URL = "http://192.168.100.178:4747/video"  # mantener
WIDTH  = 1280
HEIGHT = 720
```

### 2. Nuevo protocolo UDP — JSON con datos de detección

En lugar de enviar "1" o "0", enviar por UDP puerto 5005 el siguiente JSON en cada frame:

```python
payload = {
    "personas": count_personas,           # int: cantidad de personas detectadas
    "pies": [[x1, y1], [x2, y2], ...],   # list[list[int]]: coordenadas de pies de cada persona
    "frame_id": frame_counter             # int: contador incremental de frames
}
```

Dónde `pies` se construye así (ya está en el código actual, solo extraerlo):
```python
centro_pies_x = int((x1 + x2) / 2)
centro_pies_y = y2  # punto más bajo del bounding box
```

El envío es UDP, igual que antes:
```python
self.sock.sendto(json.dumps(payload).encode(), (HMI_IP, UDP_PORT))
```

Agregar `import json` si no está (ya está en el archivo).
Agregar un contador `self.frame_counter = 0` en `CameraWorker.__init__` e
incrementarlo en cada iteración del loop.

### 3. Stream de video TCP — puerto 5010

Agregar un **servidor TCP** que transmite el frame comprimido como JPEG.
Debe correr en un hilo separado (`VideoStreamServer`, un `QThread` o `threading.Thread`).

**Protocolo de framing para TCP:**
Dado que TCP es un stream de bytes, se necesita un delimitador de longitud:
1. Comprimir frame: `_, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])`
2. Convertir a bytes: `data = buf.tobytes()`
3. Enviar primero 4 bytes con la longitud (big-endian): `len(data).to_bytes(4, 'big')`
4. Luego enviar `data`

El servidor debe:
- Escuchar en `0.0.0.0:5010`
- Aceptar una conexión a la vez (el receptor es uno solo)
- Si el cliente se desconecta, volver a escuchar sin crashear
- Acceder al frame actual via una variable compartida con lock o un `queue.Queue(maxsize=1)`

```python
import threading, queue, struct

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
            conn, _ = srv.accept()
            try:
                while True:
                    frame = self.frame_queue.get()
                    _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    data = buf.tobytes()
                    conn.sendall(len(data).to_bytes(4, 'big') + data)
            except Exception:
                conn.close()
```

En `CameraWorker`:
- Recibir `frame_queue` como parámetro del constructor
- En cada frame, hacer `self.frame_queue.put_nowait(frame_bgr)` (descartando si lleno)

En `MainWindow.__init__`:
```python
self.frame_queue = queue.Queue(maxsize=1)
self.video_server = VideoStreamServer(self.frame_queue)
self.video_server.start()
self.worker = CameraWorker(self.frame_queue)
```

### 4. Simplificar la señal de `CameraWorker`

La señal ahora emite solo lo necesario para la UI:
```python
frame_ready = Signal(QImage, int, list)  # qimg, count_personas, pies_list
```

El slot `_update_ui` en `MainWindow` actualiza:
- El video
- El contador de personas
- La lista de coordenadas de pies (mostrar como texto: "PIE 1: (640, 680)", etc.)
- El LED de estado del emisor (solo indica si YOLO está corriendo OK)

### 5. Mantener la UI existente, simplificada

Panel lateral debe mostrar:
- **PERSONAS DETECTADAS**: contador numérico grande (igual que ahora)
- **COORDENADAS DE PIES**: lista de las coordenadas enviadas (nuevo, reemplaza "ZONAS OCUPADAS")
- **ESTADO DEL SISTEMA**: LED que muestra si el emisor está transmitiendo activamente
- **METADATA**: IP destino, puertos UDP/TCP activos, dispositivo CUDA/CPU

Título: `"EMISOR — DETECCIÓN DE PERSONAS"` (actualizar desde "MONITOREO DE ZONAS")

---

## Lo que NO se toca

- La lógica YOLO: `self.model = YOLO(...)`, `self.model(frame, ...)`, el acceso a
  `results[0].masks`, `results[0].boxes`, etc.
- El dibujado de masks de segmentación sobre el frame (colores por persona)
- El dibujado de `cv2.circle` en el punto de pies
- La conversión de frame BGR → QImage para mostrar en UI
- El estilo visual general (CSS, colores industriales, fuente monospace)
- La clase `LedDot` si existe, o los estilos LED equivalentes

---

## Resumen de cambios por clase

| Clase/función         | Acción                                      |
|-----------------------|---------------------------------------------|
| `obtener_cuadrante()` | ELIMINAR                                    |
| `ZONAS_PELIGROSAS`    | ELIMINAR                                    |
| `CONFIG_PORT`         | ELIMINAR                                    |
| `ConfigWorker`        | ELIMINAR                                    |
| `CameraWorker`        | MODIFICAR: nuevo UDP JSON + frame_queue     |
| `VideoStreamServer`   | AGREGAR: nuevo servidor TCP puerto 5010     |
| `MainWindow`          | MODIFICAR: iniciar VideoStreamServer, nueva UI |
| `_update_ui`          | MODIFICAR: señal simplificada, nueva UI     |
| `_actualizar_zonas`   | ELIMINAR                                    |

---

## Dependencias requeridas

Todas ya deberían estar instaladas. Verificar que el import list incluya:
```python
import sys, json, socket, threading, queue, struct
import cv2, torch, numpy as np
from ultralytics import YOLO
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QLabel,
    QVBoxLayout, QHBoxLayout, QFrame, QSizePolicy)
```

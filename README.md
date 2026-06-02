# TP2 — Sistema de Monitoreo Mecatrónico

Sistema distribuido de detección y monitoreo de personas en zonas de riesgo, desarrollado para la materia **Sistemas Mecatrónicos**. Utiliza visión por computadora con YOLO para detección en tiempo real, comunicación por red UDP/TCP, y una interfaz HMI estilo control room industrial.

---

## Arquitectura del Sistema (v2)

El sistema está compuesto por dos módulos que corren en PCs separadas:

### Emisor

- Captura video con cámara
- Detecta personas mediante el modelo **YOLOv8**
- Envía datos de detección por **UDP puerto 5005** en formato JSON
- Transmite el stream de video comprimido JPEG por **TCP puerto 5010**
- No gestiona zonas ni lógica de alarma

### Receptor

- Recibe datos JSON por UDP y video por TCP
- Implementa un **Editor de Polígonos AR** para definir zonas de riesgo
- Realiza detección de intrusión localmente con point_in_polygon
- Persiste las zonas definidas en zonas.json
- Muestra una HMI estilo control room industrial

---

## Protocolo de Red

| Canal | Protocolo | Puerto | Descripción |
|-------|-----------|--------|-------------|
| Datos de detección | UDP | 5005 | JSON con personas, coordenadas de pies y frame_id |
| Stream de video | TCP | 5010 | Framing: 4 bytes big-endian + payload JPEG |

Formato del mensaje UDP:

```json
{"personas": 2, "pies": [[640, 680], [320, 700]], "frame_id": 1234}
```

---

## Estructura del Repositorio

```
tp2-sistemas-mecatronicos-2/
├── emisor/                 # Módulo emisor (PC con cámara)
│   ├── CodigoEmisor.py
│   └── CLAUDE.md
├── receptor/               # Módulo receptor (PC HMI)
│   ├── CodigoReceptor.py
│   └── CLAUDE.md
├── yolo26n-seg.pt          # Modelo YOLO para detección
├── zonas.json              # Zonas de riesgo (polígonos)
├── homografia.json         # Parámetros de homografía
├── run_emisor.bat          # Script de arranque del emisor
├── run_receptor.bat        # Script de arranque del receptor
└── CLAUDE.md               # Documentación general
```

---

## Requisitos

- Python 3.x
- Ultralytics YOLO
- OpenCV (cv2)
- PyQt5
- NumPy

---

## Cómo Ejecutar

**Emisor** (PC con cámara):

```bash
run_emisor.bat
```

**Receptor** (PC HMI):

```bash
run_receptor.bat
```

Iniciar primero el receptor y luego el emisor. Ambas PCs deben estar en la misma red.

---

## Características de la HMI

- Estética de control room industrial: fondo negro/gris oscuro, tipografía monospace, indicadores tipo LED
- Colores rojo/verde para estados de alarma
- Editor de zonas de riesgo con polígonos AR dibujados sobre el video en tiempo real
- Visualización de conteo de personas y coordenadas de pies detectados
- Reconexión automática del stream de video TCP

---

## Resolución de Referencia

1280 x 720 píxeles

---

## Autores

- **LMPCdia** — Lautaro Morel Penco

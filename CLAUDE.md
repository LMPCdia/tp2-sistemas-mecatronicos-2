# PROJECT MANAGER — Sistema de Monitoreo Mecatrónico

## Arquitectura (v2 — activa)
- **emisor/**: PC con cámara + YOLO. Detecta personas, envía JSON por UDP 5005 y
  stream de video comprimido JPEG por TCP 5010. No maneja zonas.
- **receptor/**: PC HMI. Recibe JSON UDP + video TCP, tiene editor de polígonos AR,
  hace point_in_polygon() localmente, guarda zonas en `zonas.json`.

## Agentes activos
- Agente 1 (emisor/): Modificar CodigoEmisor.py — ver emisor/CLAUDE.md
- Agente 2 (receptor/): Modificar CodigoReceptor.py — ver receptor/CLAUDE.md

## Estilo visual
Control room industrial. Fondo negro/gris oscuro, tipografía monospace,
indicadores tipo LED, colores rojo/verde para estados de alarma.
Sin decoración innecesaria. Funcional y claro.

## Protocolo de red

### UDP — puerto 5005 (emisor → receptor)
```json
{"personas": 2, "pies": [[640, 680], [320, 700]], "frame_id": 1234}
```

### TCP — puerto 5010 (emisor → receptor, stream de video)
Framing: 4 bytes big-endian con longitud del payload + N bytes JPEG.

## Reglas generales
- No modificar la lógica YOLO del emisor
- Resolución de referencia: 1280x720
- Las zonas son polígonos libres definidos en el receptor (no cuadrícula fija)
- Las zonas se guardan en receptor/zonas.json
- La detección de intrusión ocurre solo en el receptor (point_in_polygon)
- Mantener compatibilidad de protocolo entre emisor y receptor

## Log de cambios
- 2026-04-30: Migración a arquitectura v2. Emisor pasa a enviar JSON + TCP stream.
  Receptor pasa a editor de polígonos AR con point_in_polygon local.
- 2026-04-30 [Agente 1]: CodigoEmisor.py reescrito. Eliminados: obtener_cuadrante(),
  ZONAS_PELIGROSAS, CONFIG_PORT, ConfigWorker, lógica de cuadrantes y cuadrícula.
  Agregados: VideoStreamServer (TCP 5010, framing 4 bytes big-endian + JPEG),
  protocolo UDP JSON {personas, pies, frame_id}. Imports completados (struct, QTimer).
  UI simplificada: contador + coordenadas de pies + LED + metadata UDP/TCP.
- 2026-04-30 [Agente 2]: CodigoReceptor.py reescrito. Eliminados: CeldaZona,
  ZONAS_PELIGROSAS_DEFAULT, PUERTO_CONFIG, _enviar_config(), _reset_zonas(),
  _build_zone_editor() con grilla 3x3. Modificados: WorkerRed emite dict (JSON UDP),
  actualizar_hmi() reemplazado por _on_datos() + _actualizar_alarma(). Agregados:
  VideoWorker (TCP 5010, framing 4 bytes big-endian, reconexión automática),
  EditorPoligonos (widget AR con paintEvent, conversión coords widget/imagen,
  click izquierdo=punto, click derecho=cerrar, ESC=descartar, DEL=deshacer),
  _verificar_alarma() con cv2.pointPolygonTest(), persistencia zonas.json
  (_cargar_zonas al inicio, _guardar_zonas con botón). Pestaña EDITOR DE ZONAS AR
  reemplaza grilla 3x3. Monitor muestra personas y coordenadas de pies.
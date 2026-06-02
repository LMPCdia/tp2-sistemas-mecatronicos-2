# AGENTE 2 — Receptor (CodigoReceptor.py)

## Estado actual del archivo (v2 — activo)

`CodigoReceptor.py` ya tiene la arquitectura v2 completa y funcional:

| Clase | Estado |
|---|---|
| `WorkerRed` | UDP 5005, emite `dict` JSON |
| `VideoWorker` | TCP 5010, stream JPEG con framing 4 bytes big-endian |
| `LedDot` | LED widget, no tocar |
| `EditorPoligonos` | Editor AR de zonas en **coordenadas de píxeles** |
| `VentanaPrincipal` | Tabs MONITOR + EDITOR DE ZONAS AR, alarma, persistencia |

---

## Tarea: agregar homografía (v3)

La cámara tiene perspectiva inclinada. Los píxeles que llegan del emisor están
distorsionados. La homografía convierte píxeles → metros en el plano real del piso.
**Solo va en el receptor.** El emisor sigue enviando píxeles sin cambios.

---

## Nueva constante

```python
HOMOGRAFIA_FILE = "homografia.json"
```

---

## Utilities de homografía (funciones globales, fuera de clases)

```python
def _aplicar_H(H: np.ndarray, px: float, py: float) -> tuple[float, float]:
    """Transforma un punto (px, py) en píxeles a metros usando la matriz H."""
    pt = np.array([[[px, py]]], dtype=np.float32)
    res = cv2.perspectiveTransform(pt, H)
    return float(res[0][0][0]), float(res[0][0][1])
```

---

## Nueva clase: `CalibradorHomografia(QWidget)`

### Propósito
Widget para la pestaña "CALIBRACIÓN". El usuario selecciona 4 puntos del piso
en la imagen de cámara y les asigna coordenadas reales en metros. Con eso se
calcula la homografía.

### Señal
```python
homografia_calculada = Signal(object, object)  # (H: np.ndarray, H_inv: np.ndarray)
```

### Estado interno
```python
self._frame: QImage | None = None
self._puntos_px: list[tuple[int, int]] = []   # máx 4, en coords de imagen 1280x720
self._H: np.ndarray | None = None
# 8 QLineEdit: self._inputs[i] = (le_x, le_y) para i in 0..3
```

### Comportamiento de click
- Solo acepta clicks izquierdos mientras `len(self._puntos_px) < 4`
- Convierte coords del widget → imagen (mismo cálculo que `EditorPoligonos._widget_to_image`)
- Agrega punto a `self._puntos_px`
- Llama `self.update()`

### `paintEvent`
- Fondo negro, imagen de cámara centrada con KeepAspectRatio
- Sobre cada punto seleccionado: círculo amarillo pequeño + número (1–4)
- Instrucción: "CLICK IZQUIERDO: seleccionar punto en el piso (máx 4)"
- Si ya hay 4 puntos: "4 puntos seleccionados — ingresá las coords reales y calculá"

### Layout interno del widget de calibración
```
[Canvas de imagen — altura fija 300px, expande horizontal]
[Grid 4 filas]:
  Punto 1: PIX (---, ---) → Real X: [____] m  Y: [____] m
  Punto 2: PIX (---, ---) → Real X: [____] m  Y: [____] m
  Punto 3: PIX (---, ---) → Real X: [____] m  Y: [____] m
  Punto 4: PIX (---, ---) → Real X: [____] m  Y: [____] m
[btn: CALCULAR HOMOGRAFÍA]  [btn: LIMPIAR PUNTOS]
[lbl_status]
```

Los labels "PIX (---,---)" se actualizan en tiempo real al clickear.

### Método `_calcular()`
```python
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
        # guardar en homografia.json
        data = {
            "H": H.tolist(),
            "H_inv": H_inv.tolist(),
            "src_pts": [list(p) for p in self._puntos_px],
            "dst_pts": dst.tolist(),
        }
        with open(HOMOGRAFIA_FILE, "w") as f:
            json.dump(data, f)
        self.lbl_status.setText("✔  Homografía calculada y guardada")
        self.homografia_calculada.emit(H, H_inv)
    except Exception as exc:
        self.lbl_status.setText(f"✘  Error: {exc}")
```

### Método `_limpiar()`
```python
def _limpiar(self):
    self._puntos_px = []
    self._H = None
    self.update()
```

### Método `set_frame(qimg: QImage)`
```python
def set_frame(self, qimg: QImage):
    self._frame = qimg
    self.update()
```

---

## Modificar `EditorPoligonos`

### Nuevos atributos
```python
self._H: np.ndarray | None = None
self._H_inv: np.ndarray | None = None
```

### Nuevo método público
```python
def set_homografia(self, H: np.ndarray, H_inv: np.ndarray):
    self._H = H
    self._H_inv = H_inv
    self.update()
```

### Nuevos métodos privados de coordinadas
Reemplazar el uso directo de `_widget_to_image` y `_image_to_widget` por:

```python
def _punto_a_almacenar(self, wx: int, wy: int) -> tuple:
    """Widget coords → coordenadas de almacenamiento (metros si hay H, píxeles si no)."""
    ix, iy = self._widget_to_image(wx, wy)
    if self._H is not None:
        return _aplicar_H(self._H, float(ix), float(iy))
    return ix, iy

def _punto_a_widget(self, sx, sy) -> tuple[int, int]:
    """Coordenadas de almacenamiento → widget (invierte H si hay homografía)."""
    if self._H_inv is not None:
        ix, iy = _aplicar_H(self._H_inv, float(sx), float(sy))
    else:
        ix, iy = float(sx), float(sy)
    return self._image_to_widget(ix, iy)
```

### Cambios en `mousePressEvent`
Reemplazar:
```python
ix, iy = self._widget_to_image(wx, wy)
self.poligono_actual.append((ix, iy))
```
Por:
```python
coord = self._punto_a_almacenar(wx, wy)
self.poligono_actual.append(coord)
```

### Cambios en `paintEvent`
Reemplazar en el loop de dibujo de polígonos y del polígono actual:
```python
pts_w = [QPoint(*self._image_to_widget(ix, iy)) for ix, iy in zona]
```
Por:
```python
pts_w = [QPoint(*self._punto_a_widget(sx, sy)) for sx, sy in zona]
```

Y para el polígono en construcción:
```python
pts_w = [self._image_to_widget(ix, iy) for ix, iy in self.poligono_actual]
```
Por:
```python
pts_w = [self._punto_a_widget(sx, sy) for sx, sy in self.poligono_actual]
```

### Agregar indicador de modo en `paintEvent`
Justo después de dibujar la imagen de fondo, agregar:
```python
if self._H is not None:
    p.setPen(QPen(QColor("#00aaff")))
    p.setFont(QFont("Courier New", 8))
    p.drawText(self.rect().adjusted(8, 4, 0, 0), Qt.AlignTop | Qt.AlignLeft,
               "◈  MODO METROS — HOMOGRAFÍA ACTIVA")
```

### Cambio en `cargar_poligonos()`
La firma no cambia, pero ahora acepta float (metros) además de int (píxeles):
```python
def cargar_poligonos(self, data: list):
    self.poligonos = [[(p[0], p[1]) for p in zona] for zona in data]
    self.update()
```
(Eliminar el `int()` explícito para soportar floats de metros.)

### Tipo de `poligonos`
Cambiar el type hint:
```python
self.poligonos: list[list[tuple]] = []
self.poligono_actual: list[tuple] = []
```

---

## Modificar `_verificar_alarma()` en `VentanaPrincipal`

```python
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
```

---

## Modificar `VentanaPrincipal`

### Nuevos atributos en `__init__`
```python
self._H: np.ndarray | None = None
self._H_inv: np.ndarray | None = None
```
Y llamar `self._cargar_homografia()` después de `self._cargar_zonas()`.

### Nuevo método `_cargar_homografia()`
```python
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
        except Exception as exc:
            pass  # sin H válida → modo píxeles
```

### Nuevo slot `_on_homografia()`
```python
def _on_homografia(self, H: np.ndarray, H_inv: np.ndarray):
    self._H = H
    self._H_inv = H_inv
    self.editor.set_homografia(H, H_inv)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    self._log_entry(ts, "◈  HOMOGRAFÍA CALIBRADA — modo metros activado", "#00aaff")
    self._actualizar_indicador_modo()
```

### Nuevo método `_actualizar_indicador_modo()`
Actualiza un `QLabel` en el MONITOR que muestra el modo actual:
```python
def _actualizar_indicador_modo(self):
    if self._H is not None:
        self.lbl_modo.setText("◈  MODO: METROS")
        self.lbl_modo.setStyleSheet("color: #00aaff; font-size: 8pt; font-family: 'Courier New';")
    else:
        self.lbl_modo.setText("◌  MODO: PÍXELES (sin calibración)")
        self.lbl_modo.setStyleSheet("color: #444; font-size: 8pt; font-family: 'Courier New';")
```

### Modificar `_build_monitor()` — agregar `lbl_modo`
En `datos_row`, agregar una columna más:
```python
modo_col = QVBoxLayout()
modo_col.setSpacing(2)
lbl_modo_t = QLabel("MODO DE DETECCIÓN")
lbl_modo_t.setStyleSheet("color: #444; font-size: 7pt; letter-spacing: 2px;")
self.lbl_modo = QLabel("◌  MODO: PÍXELES (sin calibración)")
self.lbl_modo.setStyleSheet("color: #444; font-size: 8pt; font-family: 'Courier New';")
modo_col.addWidget(lbl_modo_t)
modo_col.addWidget(self.lbl_modo)
datos_row.addLayout(modo_col)
```

### Modificar `_on_datos()` — mostrar coords en metros si H disponible
```python
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
```

También actualizar el label del header de pies en `_build_monitor()`:
- El `QLabel("POSICIONES (px)")` cambia a `self.lbl_pies_titulo = QLabel("POSICIONES (px)")`,
  y en `_actualizar_indicador_modo()` actualizar su texto:
  ```python
  self.lbl_pies_titulo.setText("POSICIONES (m)" if self._H is not None else "POSICIONES (px)")
  ```

### Modificar `_build_ui()` — agregar pestaña CALIBRACIÓN
```python
tabs.addTab(self._build_calibracion(), "CALIBRACIÓN")
```

### Nuevo método `_build_calibracion()` → `QWidget`
```python
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
```

### Modificar `_start_workers()` — conectar frame a calibrador también
```python
def _start_workers(self):
    self.hilo_udp = WorkerRed()
    self.hilo_udp.datos_recibidos.connect(self._on_datos)
    self.hilo_udp.start()

    self.hilo_video = VideoWorker()
    self.hilo_video.frame_ready.connect(self.editor.set_frame)
    self.hilo_video.frame_ready.connect(self.calibrador.set_frame)  # nuevo
    self.hilo_video.start()
```

Nota: `self.calibrador` debe existir antes de `_start_workers()`. Asegurarse de que
`_build_ui()` se llama antes de `_start_workers()` en `__init__` (ya es el caso).

---

## Modificar `_cargar_zonas()` y `_guardar_zonas()`

### `_cargar_zonas()` — leer campo `"modo"`
```python
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
```

### `_guardar_zonas()` — escribir campo `"modo"`
```python
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
```

---

## Formato de archivos de persistencia

### `homografia.json`
```json
{
  "H": [[h00,h01,h02],[h10,h11,h12],[h20,h21,h22]],
  "H_inv": [[...],[...],[...]],
  "src_pts": [[px1,py1],[px2,py2],[px3,py3],[px4,py4]],
  "dst_pts": [[mx1,my1],[mx2,my2],[mx3,my3],[mx4,my4]]
}
```

### `zonas.json` (actualizado)
```json
{
  "modo": "metros",
  "poligonos": [[[0.0, 0.0], [3.0, 0.0], [3.0, 2.0], [0.0, 2.0]]]
}
```
Compatibilidad: si `"modo"` está ausente → modo píxeles (legacy).

---

## Orden de implementación

1. Agregar `HOMOGRAFIA_FILE` y `_aplicar_H()` global
2. Modificar `EditorPoligonos`: añadir `_H`/`_H_inv`, `set_homografia()`, `_punto_a_almacenar()`, `_punto_a_widget()` — actualizar `mousePressEvent` y `paintEvent`
3. Crear `CalibradorHomografia` completo
4. Agregar `self._H`, `self._H_inv` a `VentanaPrincipal.__init__`
5. Agregar `_cargar_homografia()` y llamarla en `__init__`
6. Agregar `_on_homografia()` y `_actualizar_indicador_modo()`
7. Modificar `_verificar_alarma()` para aplicar H
8. Modificar `_on_datos()` para mostrar metros
9. Agregar `lbl_modo` y `lbl_pies_titulo` a `_build_monitor()`
10. Agregar `_build_calibracion()` y tab en `_build_ui()`
11. Actualizar `_start_workers()` con conexión al calibrador
12. Actualizar `_cargar_zonas()` y `_guardar_zonas()` con campo `"modo"`

---

## Lo que NO se toca

- `WorkerRed` — sin cambios
- `VideoWorker` — sin cambios
- `LedDot` — sin cambios
- Protocolo UDP/TCP — sin cambios (CodigoEmisor.py NO se modifica)
- Mecanismo de alarma (parpadeo, log, indicador grande) — solo `_verificar_alarma()` cambia
- Estilos CSS globales — sin cambios
- `_build_zone_editor()` (estructura de botones) — sin cambios

---

## Checklist de verificación antes de entregar

- [ ] Sin H calibrada: el sistema funciona exactamente igual que antes (modo píxeles)
- [ ] Con H calibrada: el editor muestra "◈ MODO METROS — HOMOGRAFÍA ACTIVA"
- [ ] Con H calibrada: hacer click en el editor almacena metros (float) en `self.poligonos`
- [ ] Con H calibrada: los polígonos dibujados se visualizan correctamente sobre el feed
- [ ] Con H calibrada: `_verificar_alarma()` transforma pies a metros antes de `pointPolygonTest`
- [ ] Con H calibrada: `lbl_pies` en MONITOR muestra metros con 2 decimales
- [ ] `zonas.json` guardado incluye `"modo": "metros"` o `"modo": "pixeles"` según corresponda
- [ ] `homografia.json` se carga al inicio si existe
- [ ] La pestaña CALIBRACIÓN recibe frames del VideoWorker en tiempo real
- [ ] Limpiar/recalibrar H y redibujar zonas funciona sin crash

---

## Resumen de cambios por clase/método (v3)

| Clase/método | Acción |
|---|---|
| `_aplicar_H()` | AGREGAR: función global de transformación de punto con H |
| `CalibradorHomografia` | AGREGAR: nueva clase widget |
| `EditorPoligonos._H/_H_inv` | AGREGAR: atributos de homografía |
| `EditorPoligonos.set_homografia()` | AGREGAR: método público |
| `EditorPoligonos._punto_a_almacenar()` | AGREGAR: abstracción click→storage |
| `EditorPoligonos._punto_a_widget()` | AGREGAR: abstracción storage→display |
| `EditorPoligonos.mousePressEvent()` | MODIFICAR: usar `_punto_a_almacenar` |
| `EditorPoligonos.paintEvent()` | MODIFICAR: usar `_punto_a_widget` + indicador modo |
| `EditorPoligonos.cargar_poligonos()` | MODIFICAR: aceptar float |
| `VentanaPrincipal._H/_H_inv` | AGREGAR: atributos de homografía |
| `VentanaPrincipal._cargar_homografia()` | AGREGAR: persistencia al inicio |
| `VentanaPrincipal._on_homografia()` | AGREGAR: slot de señal |
| `VentanaPrincipal._actualizar_indicador_modo()` | AGREGAR: actualiza lbl_modo |
| `VentanaPrincipal._build_calibracion()` | AGREGAR: nueva pestaña |
| `VentanaPrincipal._build_ui()` | MODIFICAR: add pestaña CALIBRACIÓN |
| `VentanaPrincipal._build_monitor()` | MODIFICAR: add lbl_modo, lbl_pies_titulo |
| `VentanaPrincipal._start_workers()` | MODIFICAR: conectar frame_ready al calibrador |
| `VentanaPrincipal._verificar_alarma()` | MODIFICAR: aplicar H si disponible |
| `VentanaPrincipal._on_datos()` | MODIFICAR: mostrar metros si H disponible |
| `VentanaPrincipal._cargar_zonas()` | MODIFICAR: leer campo "modo" |
| `VentanaPrincipal._guardar_zonas()` | MODIFICAR: escribir campo "modo" |
| `WorkerRed` | SIN CAMBIOS |
| `VideoWorker` | SIN CAMBIOS |
| `LedDot` | SIN CAMBIOS |

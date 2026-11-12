"""TubeSync Mobile.

Version para Android de la parte de YouTube de MediaVault/TubeSync: pega
uno o mas links, baja el audio en MP3 320kbps (metadatos + caratula
incrustados), igual que hace la app de escritorio.

Diferencias con la version de Windows (y por que):

- En vez de invocar "yt-dlp.exe" como proceso separado, aca se usa el
  PAQUETE yt-dlp (pip, Python puro) como libreria directamente
  (yt_dlp.YoutubeDL(...).download(...)). Es el mismo motor/proyecto, los
  mismos flags -- solo que en Android no hay forma de instalar un .exe, asi
  que se usa el modulo tal cual. De yapa, la libreria da el progreso como
  diccionarios (progress_hooks/postprocessor_hooks) en vez de tener que
  parsear texto de una consola, asi que el progreso es mas confiable todavia
  que en la version de escritorio.

- ffmpeg SI es un binario real (igual que ffmpeg.exe en Windows), pero
  compilado para Android (ver recipes/ffmpeg_bin/). Se bundlea dentro del
  .apk como "libffmpeg.so" -- el nombre raro es a proposito, ver el
  docstring de ese recipe para la explicacion completa (resumen: es la unica
  forma de que Android deje EJECUTAR un binario desde Android 10 en
  adelante). yt-dlp lo invoca con --ffmpeg-location exactamente como en
  Windows, via subprocess, sin que el codigo de descarga sepa ni le importe
  la diferencia.

- Certificados HTTPS: a diferencia de Windows (que usa los certificados que
  ya trae el sistema operativo), Python compilado para Android con
  python-for-android NO tiene acceso a los certificados raiz del sistema.
  Sin esto, CUALQUIER descarga falla en el primer request HTTPS con un error
  de verificacion de certificado. Por eso aca abajo, antes de importar
  yt-dlp, se fuerza a Python a usar el paquete "certifi" (una copia de los
  certificados raiz mas comunes) como fuente de confianza.

Los archivos finales quedan en una carpeta propia de la app (no hace falta
pedir permisos de almacenamiento en ningun Android): se puede ver la ruta
exacta abajo de todo en la pantalla de la app, o con cualquier explorador
de archivos navegando a Android/data/<paquete>/files/Descargas.
"""
import os

# --- Certificados HTTPS (tiene que ir ANTES de importar yt_dlp) -----------
# En Windows, Python usa los certificados raiz que ya trae el sistema
# operativo. En Android, un Python compilado con python-for-android no
# tiene acceso a esos certificados -- el modulo "ssl" no encuentra ningun
# certificado valido y CUALQUIER request HTTPS (es decir, todo yt-dlp)
# falla en seco con "CERTIFICATE_VERIFY_FAILED", sin descargar nada. El
# paquete "certifi" trae una copia de los certificados raiz mas usados;
# estas dos variables de entorno le dicen a Python y a las librerias de
# red (urllib, requests/urllib3, que es lo que yt-dlp usa por dentro) que
# usen esa copia en vez de buscar certificados del sistema que no existen.
try:
    import certifi

    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
except Exception:
    pass

import queue
import sys
import threading
import traceback

from kivy.app import App
from kivy.clock import mainthread
from kivy.core.window import Window
from kivy.graphics import Color, RoundedRectangle
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

import yt_dlp

# --- Paleta (tema oscuro, look de app moderna) ----------------------------
COLOR_FONDO = (0.071, 0.071, 0.094, 1)        # casi negro
COLOR_TARJETA = (0.122, 0.122, 0.153, 1)       # un poco mas claro que el fondo
COLOR_ACENTO = (1, 0.322, 0.322, 1)            # rojo/coral (igual al icono) -- accion principal
COLOR_ACENTO_OSCURO = (0.75, 0.2, 0.2, 1)
COLOR_TEXTO = (0.95, 0.95, 0.96, 1)
COLOR_TEXTO_MUTE = (0.58, 0.58, 0.63, 1)
COLOR_INFO = (0.306, 0.804, 0.769, 1)           # turquesa -- "en progreso"
COLOR_OK = (0.42, 0.85, 0.55, 1)                # verde -- exito completo
COLOR_ADVERTENCIA = (0.95, 0.73, 0.25, 1)       # ambar -- listo pero con avisos
COLOR_ERROR = (1, 0.45, 0.45, 1)                # rojo -- error

Window.clearcolor = COLOR_FONDO


def ffmpeg_path():
    """Ruta al ffmpeg real empaquetado dentro del .apk (ver
    recipes/ffmpeg_bin/__init__.py). Android llena nativeLibraryDir solo
    con los archivos lib*.so que vinieron empaquetados en el .apk al
    instalarlo -- por eso ahi SI se puede ejecutar un binario, a diferencia
    de cualquier carpeta donde la app escriba algo en tiempo de ejecucion.
    En la PC (modo desarrollo, sin Android) esto no existe: se devuelve
    None y yt-dlp busca ffmpeg en el PATH del sistema, como siempre."""
    try:
        from jnius import autoclass

        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        activity = PythonActivity.mActivity
        native_dir = activity.getApplicationInfo().nativeLibraryDir
        candidato = os.path.join(native_dir, "libffmpeg.so")
        if os.path.isfile(candidato):
            return candidato
    except Exception:
        pass
    return None


# Subcarpeta DENTRO de la carpeta publica "Descargas" del telefono donde
# termina viviendo cada mp3 -- un solo lugar, nada de copias.
NOMBRE_SUBCARPETA_PUBLICA = "TubeSync Mobile"
RUTA_RELATIVA_PUBLICA = f"Download/{NOMBRE_SUBCARPETA_PUBLICA}/"


def carpeta_trabajo():
    """Carpeta de trabajo PRIVADA (getExternalFilesDir): ahi es donde
    yt-dlp/ffmpeg escriben mientras procesan un audio (y donde queda el
    log). NO es el destino final -- antes si lo era, pero eso duplicaba
    cada mp3 (uno acá + una copia en la carpeta publica de Descargas) y
    ocupaba el doble de espacio. Ahora, apenas un archivo termina de
    procesarse, se publica en la carpeta PUBLICA de Descargas (ver
    _publicar_en_musica) y el de aca se borra."""
    try:
        from jnius import autoclass

        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        activity = PythonActivity.mActivity
        base = activity.getExternalFilesDir(None).getAbsolutePath()
    except Exception:
        # Modo desarrollo en PC (sin Android): una carpeta cualquiera al lado del proyecto.
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_trabajo_dev")
    carpeta = os.path.join(base, "tmp")
    os.makedirs(carpeta, exist_ok=True)
    return carpeta


def asegurar_stdio():
    """Causa real de "error: 'str' object has no attribute 'write'": una
    app empaquetada para Android no tiene consola, y en este armado
    sys.stdout/sys.stderr quedan en un estado que no es un stream valido.
    yt-dlp escribe ahi internamente para sus propios avisos (la funcion
    write_string de yt_dlp/utils/_utils.py) AUNQUE se le pase quiet=True --
    y si sys.stderr no tiene un .write() que funcione, yt-dlp explota antes
    de poder descargar un solo byte.

    La solucion es simplemente darle a Python un sys.stdout/sys.stderr que
    SI funcionen: un archivo de log real, al lado de la carpeta de
    descargas. De paso, ese archivo sirve como registro para diagnosticar
    cualquier otro problema a futuro (se puede mandar por WhatsApp/Drive
    como cualquier otro archivo)."""
    try:
        carpeta = carpeta_trabajo()
        log_path = os.path.join(os.path.dirname(carpeta), "tubesync.log")
        log_file = open(log_path, "a", buffering=1, encoding="utf-8", errors="replace")
        sys.stdout = log_file
        sys.stderr = log_file
    except Exception:
        pass


_NOMBRES_POSTPROCESO = {
    "ExtractAudio": "extrayendo audio (mp3)...",
    "FFmpegExtractAudio": "extrayendo audio (mp3)...",
    "Metadata": "agregando metadatos...",
    "FFmpegMetadata": "agregando metadatos...",
    "EmbedThumbnail": "incrustando caratula...",
}


class Tarjeta(BoxLayout):
    """BoxLayout con fondo propio y esquinas redondeadas -- el "card" que
    se usa para el buscador de arriba y para cada fila de descarga, para
    que la app tenga aspecto de interfaz moderna en vez del gris plano de
    Kivy por default."""

    def __init__(self, color=COLOR_TARJETA, radio=16, **kwargs):
        super().__init__(**kwargs)
        with self.canvas.before:
            Color(*color)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(radio)])
        self.bind(pos=self._actualizar_rect, size=self._actualizar_rect)

    def _actualizar_rect(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size


class BotonAccion(Button):
    """Boton con esquinas redondeadas, en vez del boton gris plano default
    de Kivy. Por defecto usa el color de acento (rojo/coral), pero acepta
    color_normal/color_press para variantes (como el boton destructivo de
    "Borrar todo")."""

    def __init__(self, color_normal=None, color_press=None, **kwargs):
        super().__init__(background_normal="", background_down="", background_color=(0, 0, 0, 0), **kwargs)
        self.color = (1, 1, 1, 1)
        self.bold = True
        self._color_normal = color_normal or COLOR_ACENTO
        self._color_press = color_press or COLOR_ACENTO_OSCURO
        with self.canvas.before:
            self._color_fondo = Color(*self._color_normal)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(14)])
        self.bind(pos=self._actualizar_rect, size=self._actualizar_rect, state=self._actualizar_color)

    def _actualizar_rect(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size

    def _actualizar_color(self, *_):
        self._color_fondo.rgba = self._color_press if self.state == "down" else self._color_normal


class BarraProgreso(Widget):
    """Barra de progreso finita dibujada a mano (track gris + relleno del
    color de acento) -- el widget ProgressBar default de Kivy usa su propio
    dibujo interno que ignora cualquier color que le pongamos desde afuera,
    asi que para que la barra combine con el resto del tema la dibujamos
    nosotros mismos con un rectangulo redondeado."""

    def __init__(self, **kwargs):
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", dp(6))
        super().__init__(**kwargs)
        self.valor = 0  # 0-100
        with self.canvas:
            Color(0.22, 0.22, 0.27, 1)
            self._fondo = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(3)])
            Color(*COLOR_ACENTO)
            self._relleno = RoundedRectangle(pos=self.pos, size=(0, self.height), radius=[dp(3)])
        self.bind(pos=self._redibujar, size=self._redibujar)

    def _redibujar(self, *_):
        self._fondo.pos = self.pos
        self._fondo.size = self.size
        ancho = self.width * (self.valor / 100)
        self._relleno.pos = self.pos
        self._relleno.size = (ancho, self.height)

    def set_valor(self, valor):
        self.valor = max(0, min(100, valor))
        self._redibujar()


class Insignia(BoxLayout):
    """Chip/etiqueta de estado redondeada (EN COLA / DESCARGANDO / LISTO /
    ERROR...), como las que usan las apps modernas para mostrar un estado
    corto de un vistazo, con su propio color segun la situacion."""

    def __init__(self, **kwargs):
        super().__init__(size_hint=(None, None), height=dp(22), padding=(dp(10), 0), **kwargs)
        self.label = Label(
            font_size=dp(11), bold=True, color=(1, 1, 1, 1),
            size_hint=(None, None), height=dp(22),
        )
        self.label.bind(texture_size=self._on_texture_size)
        self.add_widget(self.label)
        with self.canvas.before:
            self._color = Color(*COLOR_TEXTO_MUTE)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(11)])
        self.bind(pos=self._redibujar, size=self._redibujar)

    def _on_texture_size(self, inst, val):
        inst.width = val[0]
        self.width = val[0] + dp(20)

    def _redibujar(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size

    def set_estado(self, texto, color):
        self.label.text = texto
        self._color.rgba = color


class BarraAcento(Widget):
    """Tira vertical de color -- acento a la izquierda de cada item de "Mis
    descargas", para diferenciarlo visualmente de las filas de descarga
    activa (que usan chip + barra de progreso)."""

    def __init__(self, color=COLOR_INFO, **kwargs):
        kwargs.setdefault("size_hint_x", None)
        kwargs.setdefault("width", dp(4))
        super().__init__(**kwargs)
        with self.canvas:
            Color(*color)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(2)])
        self.bind(pos=self._redibujar, size=self._redibujar)

    def _redibujar(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size


class BotonPestana(Button):
    """Boton selector de pestana ("Nueva descarga" / "Mis descargas"):
    resaltado con el color de acento cuando esta activo, apagado cuando
    no."""

    def __init__(self, **kwargs):
        super().__init__(
            background_normal="", background_down="", background_color=(0, 0, 0, 0),
            bold=True, font_size=dp(12), **kwargs,
        )
        self.activo = False
        with self.canvas.before:
            self._color_fondo = Color(*COLOR_TARJETA)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(12)])
        self.bind(pos=self._actualizar_rect, size=self._actualizar_rect)
        self._actualizar_estilo()

    def _actualizar_rect(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size

    def set_activo(self, activo):
        self.activo = activo
        self._actualizar_estilo()

    def _actualizar_estilo(self):
        if self.activo:
            self._color_fondo.rgba = COLOR_ACENTO
            self.color = (1, 1, 1, 1)
        else:
            self._color_fondo.rgba = COLOR_TARJETA
            self.color = COLOR_TEXTO_MUTE


class TarjetaDescarga(Tarjeta):
    """Un item de la pestana "Mis descargas": nombre del archivo + tamano,
    con una tira de acento turquesa a la izquierda."""

    def __init__(self, nombre_archivo, tam_mb, **kwargs):
        super().__init__(
            orientation="horizontal", size_hint_y=None, height=dp(58),
            padding=(dp(12), dp(8)), spacing=dp(12), **kwargs,
        )
        self.add_widget(BarraAcento())
        columna = BoxLayout(orientation="vertical", spacing=dp(2))
        titulo = Label(
            text=os.path.splitext(nombre_archivo)[0],
            color=COLOR_TEXTO, font_size=dp(13), bold=True,
            halign="left", valign="middle", shorten=True, shorten_from="right",
            size_hint_y=None, height=dp(20),
        )
        titulo.bind(size=lambda inst, val: setattr(inst, "text_size", val))
        subtitulo = Label(
            text=f"{tam_mb:.1f} MB · MP3 320kbps",
            color=COLOR_TEXTO_MUTE, font_size=dp(11),
            halign="left", valign="middle",
            size_hint_y=None, height=dp(16),
        )
        subtitulo.bind(size=lambda inst, val: setattr(inst, "text_size", val))
        columna.add_widget(titulo)
        columna.add_widget(subtitulo)
        self.add_widget(columna)


class FilaDescarga(Tarjeta):
    """Una fila de la lista: el link, una barra de progreso y un chip de
    estado -- el mismo concepto que la cola de MediaVault en Windows, pero
    con aspecto de tarjeta y un color segun la situacion (en cola / bajando
    / listo / error)."""

    def __init__(self, url, **kwargs):
        super().__init__(
            orientation="vertical", size_hint_y=None, height=dp(82), padding=(dp(14), dp(10)), spacing=dp(6), **kwargs
        )
        self.label_url = Label(
            text=url,
            size_hint_y=None,
            height=dp(20),
            halign="left",
            valign="middle",
            shorten=True,
            shorten_from="right",
            color=COLOR_TEXTO,
            font_size=dp(13),
            bold=True,
        )
        self.label_url.bind(size=lambda inst, val: setattr(inst, "text_size", val))

        self.barra = BarraProgreso()

        fila_estado = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(22), spacing=dp(8))
        self.insignia = Insignia()
        self.insignia.set_estado("EN COLA", COLOR_TEXTO_MUTE)
        self.label_detalle = Label(
            text="",
            halign="left",
            valign="middle",
            color=COLOR_TEXTO_MUTE,
            font_size=dp(11),
        )
        self.label_detalle.bind(size=lambda inst, val: setattr(inst, "text_size", val))
        fila_estado.add_widget(self.insignia)
        fila_estado.add_widget(self.label_detalle)

        self.add_widget(self.label_url)
        self.add_widget(self.barra)
        self.add_widget(fila_estado)

    @mainthread
    def set_progreso(self, valor):
        self.barra.set_valor(valor)

    @mainthread
    def set_estado(self, chip_texto, chip_color, detalle=""):
        self.insignia.set_estado(chip_texto, chip_color)
        self.label_detalle.text = detalle


class TubeSyncMobileApp(App):
    title = "TubeSync Mobile"

    def build(self):
        asegurar_stdio()
        self.cola = queue.Queue()
        self.carpeta_trabajo = carpeta_trabajo()

        raiz = BoxLayout(orientation="vertical")

        # --- Barra superior, tipo "app bar" -------------------------------
        barra_superior = BoxLayout(orientation="vertical", size_hint_y=None, height=dp(76), padding=(dp(18), dp(10)))
        with barra_superior.canvas.before:
            Color(*COLOR_TARJETA)
            self._rect_barra = RoundedRectangle(pos=barra_superior.pos, size=barra_superior.size, radius=[0])
        barra_superior.bind(
            pos=lambda i, v: setattr(self._rect_barra, "pos", v),
            size=lambda i, v: setattr(self._rect_barra, "size", v),
        )
        barra_superior.add_widget(
            Label(
                text="TubeSync Mobile",
                font_size=dp(20),
                bold=True,
                color=COLOR_TEXTO,
                halign="left",
                valign="middle",
                size_hint_y=None,
                height=dp(28),
                text_size=(Window.width - dp(36), dp(28)),
            )
        )
        barra_superior.add_widget(
            Label(
                text="Audio de YouTube en MP3 320kbps",
                font_size=dp(12),
                color=COLOR_TEXTO_MUTE,
                halign="left",
                valign="middle",
                size_hint_y=None,
                height=dp(18),
                text_size=(Window.width - dp(36), dp(18)),
            )
        )
        raiz.add_widget(barra_superior)

        # --- Contenido, con margen propio ---------------------------------
        contenido = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(12))

        # --- Selector de pestanas (Nueva descarga / Mis descargas) --------
        fila_pestanas = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(40), spacing=dp(8))
        self.boton_tab_nueva = BotonPestana(text="NUEVA DESCARGA")
        self.boton_tab_descargas = BotonPestana(text="MIS DESCARGAS")
        self.boton_tab_nueva.bind(on_release=lambda *_: self._cambiar_pestana("nueva"))
        self.boton_tab_descargas.bind(on_release=lambda *_: self._cambiar_pestana("descargas"))
        fila_pestanas.add_widget(self.boton_tab_nueva)
        fila_pestanas.add_widget(self.boton_tab_descargas)
        contenido.add_widget(fila_pestanas)

        self.area_vistas = BoxLayout(orientation="vertical")
        contenido.add_widget(self.area_vistas)

        # --- Vista "Nueva descarga" ----------------------------------------
        self.vista_nueva = BoxLayout(orientation="vertical", spacing=dp(12))

        tarjeta_input = Tarjeta(orientation="vertical", size_hint_y=None, height=dp(120), padding=dp(10), color=COLOR_TARJETA)
        self.input_links = TextInput(
            hint_text="Pega uno o mas links de YouTube (uno por linea)",
            background_normal="",
            background_active="",
            background_color=(0.16, 0.16, 0.2, 1),
            foreground_color=COLOR_TEXTO,
            hint_text_color=COLOR_TEXTO_MUTE,
            cursor_color=COLOR_ACENTO,
            padding=(dp(10), dp(10)),
            multiline=True,
        )
        tarjeta_input.add_widget(self.input_links)
        self.vista_nueva.add_widget(tarjeta_input)

        boton = BotonAccion(text="DESCARGAR AUDIO", size_hint_y=None, height=dp(50), font_size=dp(15))
        boton.bind(on_release=self.agregar_links)
        self.vista_nueva.add_widget(boton)

        self.label_vacio = Label(
            text="Todavia no agregaste ningun link.\nPegalo arriba y toca \"Descargar audio\".",
            color=COLOR_TEXTO_MUTE,
            font_size=dp(13),
            halign="center",
            valign="middle",
        )
        self.vista_nueva.add_widget(self.label_vacio)

        self.lista = GridLayout(cols=1, size_hint_y=None, spacing=dp(10))
        self.lista.bind(minimum_height=self.lista.setter("height"))
        scroll = ScrollView()
        scroll.add_widget(self.lista)
        self.vista_nueva.add_widget(scroll)

        self.vista_nueva.add_widget(
            Label(
                text=f"Se guardan en:\nDescargas / {NOMBRE_SUBCARPETA_PUBLICA}",
                size_hint_y=None,
                height=dp(46),
                font_size=dp(10.5),
                color=COLOR_TEXTO_MUTE,
                halign="center",
                valign="middle",
            )
        )

        # --- Vista "Mis descargas" ------------------------------------------
        self.vista_descargas = BoxLayout(orientation="vertical", spacing=dp(10))

        self.boton_borrar_todo = BotonAccion(
            text="BORRAR TODO", size_hint_y=None, height=dp(40), font_size=dp(12),
            color_normal=COLOR_ERROR, color_press=COLOR_ACENTO_OSCURO,
        )
        self.boton_borrar_todo.bind(on_release=lambda *_: self._confirmar_borrar_todo())
        self.vista_descargas.add_widget(self.boton_borrar_todo)

        self.label_descargas_vacio = Label(
            text="Todavia no descargaste ningun audio.",
            color=COLOR_TEXTO_MUTE,
            font_size=dp(13),
            halign="center",
            valign="middle",
        )
        self.vista_descargas.add_widget(self.label_descargas_vacio)

        self.lista_descargas = GridLayout(cols=1, size_hint_y=None, spacing=dp(10))
        self.lista_descargas.bind(minimum_height=self.lista_descargas.setter("height"))
        scroll_descargas = ScrollView()
        scroll_descargas.add_widget(self.lista_descargas)
        self.vista_descargas.add_widget(scroll_descargas)

        raiz.add_widget(contenido)

        self._cambiar_pestana("nueva")

        threading.Thread(target=self._worker, daemon=True).start()
        return raiz

    def _cambiar_pestana(self, nombre):
        self.boton_tab_nueva.set_activo(nombre == "nueva")
        self.boton_tab_descargas.set_activo(nombre == "descargas")
        self.area_vistas.clear_widgets()
        if nombre == "nueva":
            self.area_vistas.add_widget(self.vista_nueva)
        else:
            self._refrescar_descargas()
            self.area_vistas.add_widget(self.vista_descargas)

    def _consultar_descargas_publicas(self):
        """Lee la lista REAL de archivos que hay en la carpeta publica de
        Descargas (Download/TubeSync Mobile) via MediaStore -- ya no hay
        una carpeta privada propia para listar (eso duplicaba espacio), asi
        que esto es ahora la UNICA fuente de verdad de "Mis descargas"."""
        from jnius import autoclass

        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        MediaStoreDownloads = autoclass("android.provider.MediaStore$Downloads")
        activity = PythonActivity.mActivity
        resolver = activity.getContentResolver()

        proyeccion = ["_display_name", "_size"]
        cursor = resolver.query(
            MediaStoreDownloads.EXTERNAL_CONTENT_URI,
            proyeccion,
            "relative_path = ?",
            [RUTA_RELATIVA_PUBLICA],
            "_id DESC",
        )
        resultado = []
        if cursor is not None:
            try:
                idx_nombre = cursor.getColumnIndexOrThrow("_display_name")
                idx_tam = cursor.getColumnIndexOrThrow("_size")
                while cursor.moveToNext():
                    nombre = cursor.getString(idx_nombre)
                    tam_bytes = cursor.getLong(idx_tam)
                    resultado.append((nombre, tam_bytes / 1024 / 1024))
            finally:
                cursor.close()
        return resultado

    def _refrescar_descargas(self):
        """Vuelve a consultar la carpeta publica de Descargas y arma la
        lista de la pestana "Mis descargas". Se llama cada vez que se entra
        a esa pestana (no hace falta que corra sola en segundo plano)."""
        self.lista_descargas.clear_widgets()
        try:
            archivos = self._consultar_descargas_publicas()
        except Exception:
            traceback.print_exc()
            archivos = []
        hay_archivos = bool(archivos)
        self.label_descargas_vacio.opacity = 0 if hay_archivos else 1
        self.label_descargas_vacio.size_hint_y = 0 if hay_archivos else None
        self.label_descargas_vacio.height = 0 if hay_archivos else dp(50)
        for nombre, tam_mb in archivos:
            self.lista_descargas.add_widget(TarjetaDescarga(nombre, tam_mb))

    def _confirmar_borrar_todo(self):
        """Antes de borrar archivos de verdad (irreversible), pide
        confirmacion con un popup -- no hay forma de deshacer esto."""
        caja = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(14))
        caja.add_widget(
            Label(
                text="¿Borrar todos los mp3 descargados?\nEsta accion no se puede deshacer.",
                color=COLOR_TEXTO,
                halign="center",
                valign="middle",
            )
        )
        fila_botones = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(48), spacing=dp(10))
        boton_cancelar = BotonAccion(text="CANCELAR", color_normal=COLOR_TARJETA, color_press=COLOR_FONDO)
        boton_borrar = BotonAccion(text="BORRAR TODO", color_normal=COLOR_ERROR, color_press=COLOR_ACENTO_OSCURO)
        fila_botones.add_widget(boton_cancelar)
        fila_botones.add_widget(boton_borrar)
        caja.add_widget(fila_botones)

        popup = Popup(
            title="Confirmar borrado",
            content=caja,
            size_hint=(0.85, None),
            height=dp(190),
            separator_color=COLOR_ACENTO,
            background_color=COLOR_FONDO,
        )
        boton_cancelar.bind(on_release=popup.dismiss)

        def _confirmar(*_):
            popup.dismiss()
            self._borrar_todas_las_descargas()
            self._refrescar_descargas()

        boton_borrar.bind(on_release=_confirmar)
        popup.open()

    def _borrar_todas_las_descargas(self):
        """Borra de verdad (libera espacio) todos los mp3 que esta app
        publico en la carpeta de Descargas -- via MediaStore, asi
        desaparecen tambien de cualquier otra app (reproductor de musica,
        explorador de archivos), no solo de esta lista."""
        try:
            from jnius import autoclass

            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            MediaStoreDownloads = autoclass("android.provider.MediaStore$Downloads")
            activity = PythonActivity.mActivity
            resolver = activity.getContentResolver()
            resolver.delete(
                MediaStoreDownloads.EXTERNAL_CONTENT_URI,
                "relative_path = ?",
                [RUTA_RELATIVA_PUBLICA],
            )
        except Exception:
            traceback.print_exc()

    def _actualizar_estado_vacio(self):
        self.label_vacio.opacity = 0 if self.lista.children else 1
        self.label_vacio.size_hint_y = 0 if self.lista.children else None
        self.label_vacio.height = 0 if self.lista.children else dp(50)

    def agregar_links(self, *_):
        try:
            texto = self.input_links.text.strip()
            if not texto:
                return
            for linea in texto.splitlines():
                link = linea.strip()
                if not link:
                    continue
                fila = FilaDescarga(link)
                self.lista.add_widget(fila)
                self.cola.put((link, fila))
            self.input_links.text = ""
            self._actualizar_estado_vacio()
        except Exception:
            # No debería pasar nunca, pero si algo rompe aca preferimos verlo
            # en pantalla antes que tener la app "colgada" sin ninguna pista.
            traceback.print_exc()

    # --- Descarga, una por una (mismo criterio simple que una cola FIFO) ---
    #
    # Se hace en DOS pasos separados a proposito:
    #   1) Descargar + convertir a mp3 (FFmpegExtractAudio). Si esto anda,
    #      ya queda un archivo de audio usable pase lo que pase despues.
    #   2) Agregar metadatos y caratula (FFmpegMetadata + EmbedThumbnail),
    #      por separado y a "mejor esfuerzo": si este paso falla por lo que
    #      sea, el mp3 del paso 1 NO se pierde ni se borra -- la fila queda
    #      en "listo" igual, solo que sin caratula, en vez de mostrar un
    #      error y no dejar ningun archivo (como pasaba antes, cuando los
    #      3 postprocesos corrian como una sola operacion atomica).

    def _worker(self):
        while True:
            link, fila = self.cola.get()
            try:
                fila.set_estado("PREPARANDO", COLOR_INFO)
                opciones = self._opciones_descarga(fila)
                with yt_dlp.YoutubeDL(opciones) as ydl:
                    info = ydl.extract_info(link, download=True)

                archivo = info.get("filepath") or info.get("_filename")

                try:
                    fila.set_estado("PROCESANDO", COLOR_INFO, detalle="agregando metadatos y caratula...")
                    self._agregar_metadatos_y_caratula(archivo, info)
                except Exception as e2:
                    # El audio ya esta guardado (paso 1 OK) -- esto solo
                    # afecta metadatos/caratula, no es un error fatal.
                    fila.set_estado("PROCESANDO", COLOR_ADVERTENCIA, detalle=f"sin caratula/metadatos: {self._resumen_error(e2)}")

                try:
                    self._publicar_en_musica(archivo)
                    self._limpiar_temporales(archivo)
                    fila.set_estado("LISTO", COLOR_OK)
                except Exception as e3:
                    # No se pudo publicar en la carpeta publica de
                    # Descargas -- se deja el archivo de trabajo como esta
                    # (sin borrar) para no perderlo, aunque el usuario no lo
                    # vea listado en "Mis descargas" (esa lista ahora lee
                    # directamente de la carpeta publica).
                    fila.set_estado("LISTO", COLOR_ADVERTENCIA, detalle=f"no se pudo guardar en Descargas: {self._resumen_error(e3)}")

                fila.set_progreso(100)
            except Exception as e:
                fila.set_progreso(0)
                fila.set_estado("ERROR", COLOR_ERROR, detalle=self._resumen_error(e))
            finally:
                self.cola.task_done()

    @staticmethod
    def _resumen_error(e):
        """Mensaje de error + archivo:linea exacto de donde salto, para
        poder diagnosticar un fallo sin tener que reproducirlo -- la fila
        de la app solo tiene lugar para una linea de texto, asi que esto
        es deliberadamente compacto."""
        detalle = str(e) or e.__class__.__name__
        ultimo = traceback.extract_tb(e.__traceback__)
        if ultimo:
            cuadro = ultimo[-1]
            detalle += f" [{os.path.basename(cuadro.filename)}:{cuadro.lineno}]"
        return detalle

    def _opciones_descarga(self, fila):
        """Paso 1: solo descargar y convertir a mp3."""
        return {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(self.carpeta_trabajo, "%(title)s.%(ext)s"),
            "noplaylist": True,
            "ffmpeg_location": ffmpeg_path(),
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "320"},
            ],
            "writethumbnail": True,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [self._hook_progreso(fila)],
            "postprocessor_hooks": [self._hook_postproceso(fila)],
        }

    def _agregar_metadatos_y_caratula(self, archivo, info):
        """Paso 2 (a mejor esfuerzo): metadatos + caratula sobre el mp3 que
        ya genero el paso 1."""
        opciones = {
            "quiet": True,
            "no_warnings": True,
            "ffmpeg_location": ffmpeg_path(),
            "postprocessors": [
                {"key": "FFmpegMetadata"},
                {"key": "EmbedThumbnail"},
            ],
        }
        with yt_dlp.YoutubeDL(opciones) as ydl:
            ydl.post_process(archivo, info)

    def _publicar_en_musica(self, ruta_archivo):
        """Mueve (no copia) el mp3 ya terminado a la carpeta PUBLICA de
        Descargas del telefono (Download/TubeSync Mobile, via MediaStore),
        para que aparezca en la app de Archivos, en un reproductor de
        musica, o se pueda compartir por WhatsApp/etc.

        Por que hace falta esto: el archivo lo arma yt-dlp/ffmpeg en
        carpeta_trabajo(), que es almacenamiento PRIVADO de la app -- desde
        Android 11 en adelante ninguna otra app (ni el explorador de
        archivos del telefono) puede ver esa carpeta. La carpeta PUBLICA de
        Descargas si es visible para todo el mundo, y escribir ahi via
        MediaStore (en vez de un path de archivo directo) no pide NINGUN
        permiso especial desde Android 10 en adelante -- es, literalmente,
        el mecanismo que Android dejo pensado para este caso exacto
        (guardar contenido para el usuario sin permisos de almacenamiento
        amplios). Es un "mover" y no un "copiar": el llamador borra el
        archivo de trabajo despues de que esto termine bien (ver
        _limpiar_temporales), asi no queda duplicado ocupando el doble de
        espacio."""
        from jnius import autoclass

        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        ContentValues = autoclass("android.content.ContentValues")
        MediaStoreDownloads = autoclass("android.provider.MediaStore$Downloads")
        activity = PythonActivity.mActivity
        resolver = activity.getContentResolver()

        nombre_archivo = os.path.basename(ruta_archivo)

        valores = ContentValues()
        valores.put("_display_name", nombre_archivo)
        valores.put("mime_type", "audio/mpeg")
        valores.put("relative_path", RUTA_RELATIVA_PUBLICA)
        valores.put("is_pending", 1)

        uri_nuevo = resolver.insert(MediaStoreDownloads.EXTERNAL_CONTENT_URI, valores)
        if uri_nuevo is None:
            raise RuntimeError("ContentResolver.insert() en MediaStore devolvio null")

        flujo_salida = resolver.openOutputStream(uri_nuevo)
        try:
            with open(ruta_archivo, "rb") as f:
                while True:
                    bloque = f.read(256 * 1024)
                    if not bloque:
                        break
                    flujo_salida.write(bloque)
        finally:
            flujo_salida.close()

        valores_final = ContentValues()
        valores_final.put("is_pending", 0)
        resolver.update(uri_nuevo, valores_final, None, None)

    @staticmethod
    def _limpiar_temporales(ruta_archivo):
        """Borra el mp3 de trabajo (y cualquier archivo que haya quedado al
        lado con el mismo nombre -- por ejemplo una caratula suelta si el
        paso de metadatos fallo a mitad de camino) una vez que ya quedo
        publicado en la carpeta publica de Descargas. Sin esto, cada
        descarga quedaria duplicada (una copia aca + una en Descargas)."""
        base, _ext = os.path.splitext(ruta_archivo)
        carpeta = os.path.dirname(ruta_archivo)
        nombre_base = os.path.basename(base)
        try:
            for nombre in os.listdir(carpeta):
                if os.path.splitext(nombre)[0] == nombre_base:
                    try:
                        os.remove(os.path.join(carpeta, nombre))
                    except OSError:
                        pass
        except OSError:
            pass

    def _hook_progreso(self, fila):
        def hook(d):
            estado = d.get("status")
            if estado == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                descargado = d.get("downloaded_bytes") or 0
                if total:
                    pct = min(99, int(descargado * 100 / total))
                    fila.set_progreso(pct)
                    partes = [f"{pct}%", f"de {total / 1024 / 1024:.1f}MB"]
                    velocidad = d.get("speed")
                    if velocidad:
                        partes.append(f"(~{velocidad / 1024 / 1024:.1f}MB/s)")
                    fila.set_estado("DESCARGANDO", COLOR_INFO, detalle=" ".join(partes))
                else:
                    fila.set_estado("DESCARGANDO", COLOR_INFO)
            elif estado == "finished":
                fila.set_estado("PROCESANDO", COLOR_INFO)
        return hook

    def _hook_postproceso(self, fila):
        def hook(d):
            if d.get("status") == "started":
                nombre = d.get("postprocessor", "")
                detalle = _NOMBRES_POSTPROCESO.get(nombre, f"procesando ({nombre})...")
                fila.set_estado("PROCESANDO", COLOR_INFO, detalle=detalle)
        return hook


if __name__ == "__main__":
    TubeSyncMobileApp().run()

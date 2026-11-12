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
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

import yt_dlp

# --- Paleta (tema oscuro, look de app moderna) ----------------------------
COLOR_FONDO = (0.071, 0.071, 0.094, 1)        # casi negro
COLOR_TARJETA = (0.122, 0.122, 0.153, 1)       # un poco mas claro que el fondo
COLOR_ACENTO = (1, 0.322, 0.322, 1)            # rojo/coral (igual al icono)
COLOR_ACENTO_OSCURO = (0.75, 0.2, 0.2, 1)
COLOR_TEXTO = (0.95, 0.95, 0.96, 1)
COLOR_TEXTO_MUTE = (0.58, 0.58, 0.63, 1)
COLOR_OK = (0.42, 0.85, 0.55, 1)
COLOR_ERROR = (1, 0.45, 0.45, 1)

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


def carpeta_descargas():
    """Carpeta donde se guardan los MP3 finales. Es la carpeta EXTERNA
    propia de la app (getExternalFilesDir) -- a diferencia del
    almacenamiento compartido (Download/Music de todo el telefono), escribir
    ahi no pide ningun permiso especial en ninguna version de Android."""
    try:
        from jnius import autoclass

        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        activity = PythonActivity.mActivity
        base = activity.getExternalFilesDir(None).getAbsolutePath()
    except Exception:
        # Modo desarrollo en PC (sin Android): una carpeta cualquiera al lado del proyecto.
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_descargas_dev")
    carpeta = os.path.join(base, "Descargas")
    os.makedirs(carpeta, exist_ok=True)
    return carpeta


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
    """Boton con el color de acento y esquinas redondeadas, en vez del
    boton gris plano default de Kivy."""

    def __init__(self, **kwargs):
        super().__init__(background_normal="", background_down="", background_color=(0, 0, 0, 0), **kwargs)
        self.color = (1, 1, 1, 1)
        self.bold = True
        with self.canvas.before:
            self._color_fondo = Color(*COLOR_ACENTO)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(14)])
        self.bind(pos=self._actualizar_rect, size=self._actualizar_rect, state=self._actualizar_color)

    def _actualizar_rect(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size

    def _actualizar_color(self, *_):
        self._color_fondo.rgba = COLOR_ACENTO_OSCURO if self.state == "down" else COLOR_ACENTO


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


class FilaDescarga(Tarjeta):
    """Una fila de la lista: el link, una barra de progreso y un mensaje de
    estado -- el mismo concepto que la cola de MediaVault en Windows, pero
    con aspecto de tarjeta y un icono segun el estado (en cola / bajando /
    listo / error)."""

    def __init__(self, url, **kwargs):
        super().__init__(
            orientation="vertical", size_hint_y=None, height=dp(78), padding=(dp(14), dp(10)), spacing=dp(4), **kwargs
        )
        self.label_url = Label(
            text=url,
            size_hint_y=None,
            height=dp(22),
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

        self.label_estado = Label(
            text="en cola...",
            size_hint_y=None,
            height=dp(20),
            halign="left",
            valign="middle",
            color=COLOR_TEXTO_MUTE,
            font_size=dp(12),
        )
        self.label_estado.bind(size=lambda inst, val: setattr(inst, "text_size", val))

        self.add_widget(self.label_url)
        self.add_widget(self.barra)
        self.add_widget(self.label_estado)

    @mainthread
    def set_progreso(self, valor):
        self.barra.set_valor(valor)

    @mainthread
    def set_mensaje(self, texto, color=None):
        self.label_estado.text = texto
        self.label_estado.color = color or COLOR_TEXTO_MUTE


class TubeSyncMobileApp(App):
    title = "TubeSync Mobile"

    def build(self):
        self.cola = queue.Queue()
        self.carpeta_destino = carpeta_descargas()

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
        contenido.add_widget(tarjeta_input)

        boton = BotonAccion(text="DESCARGAR AUDIO", size_hint_y=None, height=dp(50), font_size=dp(15))
        boton.bind(on_release=self.agregar_links)
        contenido.add_widget(boton)

        self.label_vacio = Label(
            text="Todavia no agregaste ningun link.\nPegalo arriba y toca \"Descargar audio\".",
            color=COLOR_TEXTO_MUTE,
            font_size=dp(13),
            halign="center",
            valign="middle",
        )
        contenido.add_widget(self.label_vacio)

        self.lista = GridLayout(cols=1, size_hint_y=None, spacing=dp(10))
        self.lista.bind(minimum_height=self.lista.setter("height"))
        scroll = ScrollView()
        scroll.add_widget(self.lista)
        contenido.add_widget(scroll)

        contenido.add_widget(
            Label(
                text=f"Se guardan en:\n{self.carpeta_destino}",
                size_hint_y=None,
                height=dp(46),
                font_size=dp(10.5),
                color=COLOR_TEXTO_MUTE,
                halign="center",
                valign="middle",
            )
        )

        raiz.add_widget(contenido)

        threading.Thread(target=self._worker, daemon=True).start()
        return raiz

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

    def _worker(self):
        while True:
            link, fila = self.cola.get()
            try:
                fila.set_mensaje("preparando descarga...")
                opciones = self._opciones_ydl(fila)
                with yt_dlp.YoutubeDL(opciones) as ydl:
                    ydl.download([link])
                fila.set_progreso(100)
                fila.set_mensaje("listo", color=COLOR_OK)
            except Exception as e:
                fila.set_progreso(0)
                detalle = str(e) or e.__class__.__name__
                fila.set_mensaje(f"error: {detalle}", color=COLOR_ERROR)
            finally:
                self.cola.task_done()

    def _opciones_ydl(self, fila):
        return {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(self.carpeta_destino, "%(title)s.%(ext)s"),
            "noplaylist": True,
            "ffmpeg_location": ffmpeg_path(),
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "320"},
                {"key": "FFmpegMetadata"},
                {"key": "EmbedThumbnail"},
            ],
            "writethumbnail": True,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [self._hook_progreso(fila)],
            "postprocessor_hooks": [self._hook_postproceso(fila)],
        }

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
                    fila.set_mensaje(" ".join(partes))
                else:
                    fila.set_mensaje("descargando...")
            elif estado == "finished":
                fila.set_mensaje("procesando audio...")
        return hook

    def _hook_postproceso(self, fila):
        def hook(d):
            if d.get("status") == "started":
                nombre = d.get("postprocessor", "")
                fila.set_mensaje(_NOMBRES_POSTPROCESO.get(nombre, f"procesando ({nombre})..."))
        return hook


if __name__ == "__main__":
    TubeSyncMobileApp().run()

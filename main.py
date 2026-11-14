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

Los archivos finales quedan en la carpeta PUBLICA de Descargas del
telefono, adentro de una subcarpeta propia (Descargas/TubeSync Mobile) --
visibles para cualquier explorador de archivos o reproductor de musica,
sin tener que pedir permisos de almacenamiento en ningun Android (se
publican via MediaStore, ver _publicar_en_musica). No queda ninguna copia
duplicada: el archivo de trabajo temporal se borra apenas termina de
publicarse.
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

import difflib
import json
import queue
import re
import ssl
import subprocess
import sys
import threading
import traceback
import unicodedata
import urllib.parse
import urllib.request

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
}

# Un User-Agent de navegador normal para los pedidos HTTP a Spotify (ver
# soporte de links de Spotify, mas abajo) -- sin esto, algunos sitios
# (Spotify incluido) bloquean o le dan una respuesta distinta al User-Agent
# por default de Python ("Python-urllib/3.x").
_CABECERAS_HTTP = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
}

# Contexto SSL para los pedidos HTTPS propios (Spotify) -- las variables de
# entorno SSL_CERT_FILE / REQUESTS_CA_BUNDLE de mas arriba (antes de
# "import yt_dlp") le alcanzan a yt-dlp porque yt-dlp arma su PROPIO
# contexto SSL con certifi (yt_dlp/networking/_helper.py: ssl_load_certs),
# sin depender de esas variables de entorno en absoluto. Un
# urllib.request.urlopen() llamado directo (como el de _metadata_spotify /
# _forzar_caratula, mas abajo) en cambio SI depende de que el "ssl" de
# Python encuentre certificados raiz por su cuenta -- y en Android,
# compilado con python-for-android, no los encuentra (no hay variables de
# entorno que valgan para eso), asi que sin pasarle un contexto armado a
# mano con certifi (igual que hace yt-dlp), CUALQUIER pedido HTTPS propio
# falla en seco con CERTIFICATE_VERIFY_FAILED -- esto fue confirmado
# reproduciendo el mismo fallo con un contexto SSL vacio contra un sitio
# HTTPS cualquiera, y viendo que cargar un certificado raiz valido a mano
# (via load_verify_locations) lo arregla.
try:
    _CONTEXTO_SSL = ssl.create_default_context()
    _CONTEXTO_SSL.load_verify_locations(cafile=certifi.where())
except Exception:
    _CONTEXTO_SSL = None


# --- Listeners de android.media.MediaPlayer (reproductor continuo) --------
#
# MediaPlayer.setOnPreparedListener/setOnCompletionListener piden una
# instancia de una INTERFAZ Java (OnPreparedListener/OnCompletionListener,
# con un solo metodo cada una) -- pyjnius permite implementar esas
# interfaces desde Python con PythonJavaClass + el decorador @java_method
# (la firma JNI exacta, tipo "(Landroid/media/MediaPlayer;)V" para un
# metodo que recibe un MediaPlayer y no devuelve nada). Se verifico este
# mecanismo con un ejemplo minimo local (una interfaz Java de prueba
# compilada a mano, sin nada de Android) antes de usarlo aca: tanto
# invocar el callback al toque como GUARDAR la referencia al listener y
# llamarlo recien despues (que es exactamente lo que hace MediaPlayer por
# dentro) funcionan como se espera.
#
# Los callbacks de MediaPlayer pueden llegar en un hilo interno de Android,
# no en el hilo de Kivy -- por eso ambos reenvian el aviso con @mainthread
# (_al_quedar_preparado / _al_terminar_pista en TubeSyncMobileApp) en vez
# de tocar la UI directo desde aca.
#
# El resto del archivo importa "jnius" adentro de cada metodo (no aca
# arriba) porque esas llamadas son todas autoclass() sueltas. Estas dos
# clases, en cambio, necesitan EXTENDER PythonJavaClass en la definicion
# de la clase misma (no se puede dejar para "mas adelante, cuando haga
# falta") -- por eso el import esta aca, pero protegido igual que
# _CONTEXTO_SSL mas arriba, para que el archivo siga siendo importable
# (por ejemplo para pruebas con ast/exec) en un entorno sin pyjnius real.
try:
    from jnius import PythonJavaClass, java_method

    class _EscuchadorPreparado(PythonJavaClass):
        """android.media.MediaPlayer.OnPreparedListener -- avisa cuando el
        MediaPlayer ya termino de prepararse (prepareAsync) y recien ahi es
        seguro arrancar la reproduccion con start()."""

        __javainterfaces__ = ['android/media/MediaPlayer$OnPreparedListener']

        def __init__(self, app):
            super().__init__()
            self._app = app

        @java_method('(Landroid/media/MediaPlayer;)V')
        def onPrepared(self, mp):
            self._app._al_quedar_preparado()

    class _EscuchadorFinDePista(PythonJavaClass):
        """android.media.MediaPlayer.OnCompletionListener -- avisa cuando el
        tema actual termino SOLO, de principio a fin (no por pausa/stop
        manual). Esto es lo que hace que el reproductor sea "continuo": en
        vez de quedarse ahi parado, pasa sola al siguiente tema de la
        lista."""

        __javainterfaces__ = ['android/media/MediaPlayer$OnCompletionListener']

        def __init__(self, app):
            super().__init__()
            self._app = app

        @java_method('(Landroid/media/MediaPlayer;)V')
        def onCompletion(self, mp):
            self._app._al_terminar_pista()
except Exception:
    _EscuchadorPreparado = None
    _EscuchadorFinDePista = None


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


class BotonIcono(Button):
    """Boton chico sin fondo solido (solo el glifo, tipo "icono") -- para
    las acciones por-fila de "Mis descargas" (reproducir/pausar, borrar).
    BotonAccion esta pensado para la accion principal de una pantalla
    entera (ocupa todo el ancho, fondo solido); aca al lado del nombre del
    archivo eso se veria pesado, asi que este boton es solo texto+color,
    con un cambio de color sutil al tocarlo en vez de un fondo que aparece."""

    def __init__(self, color_normal=None, color_press=None, **kwargs):
        kwargs.setdefault("size_hint", (None, None))
        kwargs.setdefault("size", (dp(36), dp(36)))
        kwargs.setdefault("font_size", dp(18))
        super().__init__(background_normal="", background_down="", background_color=(0, 0, 0, 0), **kwargs)
        self.bold = True
        self._color_normal = color_normal or COLOR_TEXTO
        self._color_press = color_press or COLOR_ACENTO
        self.color = self._color_normal
        self.bind(state=self._actualizar_color)

    def _actualizar_color(self, *_):
        self.color = self._color_press if self.state == "down" else self._color_normal


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
    activa (que usan chip + barra de progreso). Guarda la instruccion
    Color (self._color) para poder cambiarle el color despues con
    set_color -- se usa para resaltar en la lista cual tema es el que
    esta sonando ahora."""

    def __init__(self, color=COLOR_INFO, **kwargs):
        kwargs.setdefault("size_hint_x", None)
        kwargs.setdefault("width", dp(4))
        super().__init__(**kwargs)
        with self.canvas:
            self._color = Color(*color)
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
    con una tira de acento a la izquierda (turquesa normalmente, acento
    rojo/coral cuando ESTE es el tema que esta sonando), un boton de
    reproducir/pausar y un boton de borrar individual (a diferencia de
    "BORRAR TODO", que borra la lista entera)."""

    def __init__(self, id_mediastore, nombre_archivo, tam_mb,
                 en_reproduccion=False, pausado=False, al_reproducir=None, al_borrar=None, **kwargs):
        super().__init__(
            orientation="horizontal", size_hint_y=None, height=dp(58),
            padding=(dp(12), dp(8)), spacing=dp(10), **kwargs,
        )
        self.id_mediastore = id_mediastore
        self.nombre_archivo = nombre_archivo

        self.acento = BarraAcento()
        self.add_widget(self.acento)

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

        self.boton_play = BotonIcono(text="")
        if al_reproducir:
            self.boton_play.bind(on_release=lambda *_: al_reproducir())
        self.add_widget(self.boton_play)

        boton_borrar = BotonIcono(text="x", color_normal=COLOR_TEXTO_MUTE, color_press=COLOR_ERROR)
        if al_borrar:
            boton_borrar.bind(on_release=lambda *_: al_borrar())
        self.add_widget(boton_borrar)

        self.set_en_reproduccion(en_reproduccion, pausado)

    def set_en_reproduccion(self, activo, pausado=False):
        """Pinta esta tarjeta como "la que esta sonando ahora" (tira de
        acento rojo/coral en vez de turquesa) y cambia el glifo del boton
        de reproducir segun corresponda: ► para arrancar/reanudar, ❙❙ para
        pausar la que ya esta sonando."""
        self.acento._color.rgba = COLOR_ACENTO if activo else COLOR_INFO
        self.boton_play.text = "▶" if (not activo or pausado) else "❙❙"


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
            hint_text="Pega uno o mas links de YouTube o Spotify (uno por linea)",
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

        # --- Barra del reproductor continuo (abajo de "Mis descargas") ----
        # Arrancan ocultas (altura 0) -- recien se muestran cuando se pone a
        # sonar el primer tema (ver _mostrar_barra_reproductor).
        self.barra_reproductor = Tarjeta(
            orientation="horizontal", size_hint_y=None, height=0, opacity=0,
            padding=(dp(14), dp(8)), spacing=dp(10),
        )
        self.label_pista_actual = Label(
            text="", color=COLOR_TEXTO, font_size=dp(12), bold=True,
            halign="left", valign="middle", shorten=True, shorten_from="right",
        )
        self.label_pista_actual.bind(size=lambda inst, val: setattr(inst, "text_size", val))
        self.boton_pista_anterior = BotonIcono(text="⏮")
        self.boton_pista_anterior.bind(on_release=lambda *_: self._pista_anterior())
        self.boton_play_pausa_barra = BotonIcono(text="▶")
        self.boton_play_pausa_barra.bind(on_release=lambda *_: self._alternar_reproduccion_actual())
        self.boton_pista_siguiente = BotonIcono(text="⏭")
        self.boton_pista_siguiente.bind(on_release=lambda *_: self._siguiente_pista())
        self.barra_reproductor.add_widget(self.label_pista_actual)
        self.barra_reproductor.add_widget(self.boton_pista_anterior)
        self.barra_reproductor.add_widget(self.boton_play_pausa_barra)
        self.barra_reproductor.add_widget(self.boton_pista_siguiente)
        self.vista_descargas.add_widget(self.barra_reproductor)

        raiz.add_widget(contenido)

        self._cambiar_pestana("nueva")

        # --- Estado del reproductor continuo -------------------------------
        # self._archivos_actuales: la MISMA lista que esta mostrando "Mis
        #   descargas" ahora (id, nombre, tam_mb) -- el orden define en que
        #   orden "siguiente/anterior" avanzan.
        # self._id_reproduciendo: id de MediaStore del tema actual, o None
        #   si no hay nada sonando.
        # self._reproductor: el android.media.MediaPlayer real (via
        #   pyjnius), o None.
        # self._tarjetas_por_id: id -> TarjetaDescarga visible ahora mismo,
        #   para poder resaltar/despintar sin reconstruir toda la lista.
        self._archivos_actuales = []
        self._id_reproduciendo = None
        self._reproductor = None
        self._pausado = False
        self._tarjetas_por_id = {}

        threading.Thread(target=self._worker, daemon=True).start()
        return raiz

    def on_stop(self):
        """Libera el MediaPlayer nativo al cerrar la app -- sin esto el
        objeto Java queda vivo en el proceso aunque Python ya no lo use."""
        self._detener_reproduccion()

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

        # "_id" (el id de MediaStore de cada fila) hace falta para poder
        # reproducir/borrar UN solo tema despues (armar su content:// Uri
        # exacto con ContentUris.withAppendedId) -- antes no se pedia
        # porque "Borrar todo" borra por relative_path, sin necesitar el id
        # de cada fila individual.
        proyeccion = ["_id", "_display_name", "_size"]
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
                idx_id = cursor.getColumnIndexOrThrow("_id")
                idx_nombre = cursor.getColumnIndexOrThrow("_display_name")
                idx_tam = cursor.getColumnIndexOrThrow("_size")
                while cursor.moveToNext():
                    id_mediastore = cursor.getLong(idx_id)
                    nombre = cursor.getString(idx_nombre)
                    tam_bytes = cursor.getLong(idx_tam)
                    resultado.append((id_mediastore, nombre, tam_bytes / 1024 / 1024))
            finally:
                cursor.close()
        return resultado

    def _refrescar_descargas(self):
        """Vuelve a consultar la carpeta publica de Descargas y arma la
        lista de la pestana "Mis descargas". Se llama cada vez que se entra
        a esa pestana (no hace falta que corra sola en segundo plano).

        Si hay un tema sonando, lo vuelve a resaltar en la lista nueva
        (buscandolo por id, no por posicion -- la posicion puede cambiar si
        se agrego o borro algo)."""
        self.lista_descargas.clear_widgets()
        self._tarjetas_por_id = {}
        try:
            archivos = self._consultar_descargas_publicas()
        except Exception:
            traceback.print_exc()
            archivos = []
        self._archivos_actuales = archivos
        hay_archivos = bool(archivos)
        self.label_descargas_vacio.opacity = 0 if hay_archivos else 1
        self.label_descargas_vacio.size_hint_y = 0 if hay_archivos else None
        self.label_descargas_vacio.height = 0 if hay_archivos else dp(50)
        for id_mediastore, nombre, tam_mb in archivos:
            tarjeta = TarjetaDescarga(
                id_mediastore, nombre, tam_mb,
                en_reproduccion=(id_mediastore == self._id_reproduciendo),
                pausado=self._pausado if id_mediastore == self._id_reproduciendo else False,
                al_reproducir=lambda id_mediastore=id_mediastore: self._tocar_o_pausar(id_mediastore),
                al_borrar=lambda id_mediastore=id_mediastore, nombre=nombre: self._confirmar_borrar_uno(id_mediastore, nombre),
            )
            self._tarjetas_por_id[id_mediastore] = tarjeta
            self.lista_descargas.add_widget(tarjeta)

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
            self._detener_reproduccion()  # va a borrar TODO, incluido lo que esta sonando
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

    def _confirmar_borrar_uno(self, id_mediastore, nombre_archivo):
        """Lo mismo que _confirmar_borrar_todo pero para UN solo archivo --
        pedido explicito de Edgar ("podés agregar la opción de botar temas
        sueltos? Ahora borra todo junto"). Mismo popup de confirmacion,
        mismo estilo, pero borra solo esta fila."""
        caja = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(14))
        caja.add_widget(
            Label(
                text=f"¿Borrar \"{os.path.splitext(nombre_archivo)[0]}\"?\nEsta accion no se puede deshacer.",
                color=COLOR_TEXTO,
                halign="center",
                valign="middle",
            )
        )
        fila_botones = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(48), spacing=dp(10))
        boton_cancelar = BotonAccion(text="CANCELAR", color_normal=COLOR_TARJETA, color_press=COLOR_FONDO)
        boton_borrar = BotonAccion(text="BORRAR", color_normal=COLOR_ERROR, color_press=COLOR_ACENTO_OSCURO)
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
            if id_mediastore == self._id_reproduciendo:
                self._detener_reproduccion()  # se esta borrando el tema que suena ahora
            self._borrar_una_descarga(id_mediastore)
            self._refrescar_descargas()

        boton_borrar.bind(on_release=_confirmar)
        popup.open()

    def _borrar_una_descarga(self, id_mediastore):
        """Borra un solo archivo (una sola fila de MediaStore) por su id,
        a diferencia de _borrar_todas_las_descargas que borra por
        relative_path (todo lo que hay en la subcarpeta). Se arma el Uri
        exacto de esa fila con ContentUris.withAppendedId -- el mismo
        patron que recomienda la documentacion de Android para apuntar a
        un item puntual de una coleccion de MediaStore."""
        try:
            from jnius import autoclass

            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            MediaStoreDownloads = autoclass("android.provider.MediaStore$Downloads")
            ContentUris = autoclass("android.content.ContentUris")
            activity = PythonActivity.mActivity
            resolver = activity.getContentResolver()
            uri_item = ContentUris.withAppendedId(MediaStoreDownloads.EXTERNAL_CONTENT_URI, id_mediastore)
            resolver.delete(uri_item, None, None)
        except Exception:
            traceback.print_exc()

    # --- Reproductor continuo -------------------------------------------
    #
    # Edgar lo pidio explicitamente: "No tengo en mi celu un reproductor
    # continuo... Es posible que de descargar pueda reproducir los temas
    # descargados cómo cualquier reproductor normal?". Se implementa con
    # android.media.MediaPlayer nativo (via pyjnius), NO con
    # kivy.core.audio.SoundLoader, por una razon concreta de permisos:
    #
    # Esta app no declara NINGUN permiso de almacenamiento (ver
    # android.permissions en buildozer.spec -- solo esta INTERNET). Eso
    # funciona porque los mp3 se escriben en la carpeta publica de
    # Descargas via MediaStore (resolver.insert + openOutputStream, ver
    # _publicar_en_musica), y bajo "scoped storage" (Android 10+) una app
    # tiene acceso de lectura/escritura/borrado SIN permisos especiales a
    # las filas de MediaStore que ELLA MISMA creo -- eso es justamente lo
    # que ya hace _borrar_todas_las_descargas/_consultar_descargas_publicas
    # con exito. Pero ese acceso es a traves del content:// Uri, NO de un
    # path de archivo de verdad (eso es la esencia de scoped storage). Un
    # SoundLoader de Kivy espera un path de archivo normal, no un content
    # Uri -- usarlo aca obligaria a conseguir un path real, lo cual
    # necesitaria permisos amplios de almacenamiento que hoy esta app no
    # tiene (y pedirlos es justo lo que "scoped storage" esta pensado para
    # evitar).
    #
    # android.media.MediaPlayer, en cambio, tiene un metodo pensado
    # EXACTAMENTE para este caso: setDataSource(Context, Uri) -- soporta
    # content:// Uris de forma nativa y oficial (documentado en
    # developer.android.com/media/platform/mediaplayer/resolver). Como ya
    # se usa pyjnius en el resto del archivo para hablar con MediaStore, es
    # el mismo mecanismo, sin agregar ninguna libreria nueva ni pedir
    # ningun permiso nuevo.

    def _obtener_uri_mediastore(self, id_mediastore):
        from jnius import autoclass

        MediaStoreDownloads = autoclass("android.provider.MediaStore$Downloads")
        ContentUris = autoclass("android.content.ContentUris")
        return ContentUris.withAppendedId(MediaStoreDownloads.EXTERNAL_CONTENT_URI, id_mediastore)

    def _tocar_o_pausar(self, id_mediastore):
        """Lo que llama el boton de play/pausa de UNA fila de la lista (o
        el de la barra de abajo, para el tema actual): si ya es el tema
        que esta sonando, alterna pausa/reanudar; si es otro distinto,
        arranca ese desde cero (soltando el anterior)."""
        if id_mediastore == self._id_reproduciendo:
            self._alternar_reproduccion_actual()
        else:
            self._iniciar_reproduccion(id_mediastore)

    def _alternar_reproduccion_actual(self):
        """Pausa/reanuda el tema que ya esta cargado -- no crea un
        MediaPlayer nuevo, asi que es instantaneo (sin el pequeno delay de
        volver a prepararlo)."""
        if self._reproductor is None:
            return
        try:
            if self._pausado:
                self._reproductor.start()
                self._pausado = False
            else:
                self._reproductor.pause()
                self._pausado = True
        except Exception:
            traceback.print_exc()
            return
        self._actualizar_ui_reproductor()

    def _iniciar_reproduccion(self, id_mediastore):
        """Arranca (desde cero) la reproduccion del tema con ese id de
        MediaStore -- suelta el MediaPlayer anterior si habia uno
        (MediaPlayer no se reutiliza entre archivos distintos, hay que
        armar uno nuevo por cada datasource)."""
        self._liberar_reproductor_actual()

        datos = next((a for a in self._archivos_actuales if a[0] == id_mediastore), None)
        if datos is None:
            return
        _id, nombre, _tam = datos

        try:
            from jnius import autoclass

            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            MediaPlayer = autoclass("android.media.MediaPlayer")
            activity = PythonActivity.mActivity
            uri = self._obtener_uri_mediastore(id_mediastore)

            reproductor = MediaPlayer()
            reproductor.setOnPreparedListener(_EscuchadorPreparado(self))
            reproductor.setOnCompletionListener(_EscuchadorFinDePista(self))
            reproductor.setDataSource(activity, uri)
            # prepareAsync() (no prepare()) para no bloquear el hilo de UI
            # de Kivy ni un instante -- arranca a sonar solo una vez que
            # termine de prepararse, en _EscuchadorPreparado.
            reproductor.prepareAsync()
        except Exception:
            traceback.print_exc()
            return

        self._reproductor = reproductor
        self._id_reproduciendo = id_mediastore
        self._pausado = False
        self.label_pista_actual.text = os.path.splitext(nombre)[0]
        self._mostrar_barra_reproductor(True)
        self._actualizar_ui_reproductor()

    @mainthread
    def _al_quedar_preparado(self):
        """Callback (ya reencolado en el hilo de Kivy, ver
        _EscuchadorPreparado) de que el MediaPlayer ya termino de
        prepararse -- recien aca es seguro llamar start()."""
        if self._reproductor is None:
            return
        try:
            self._reproductor.start()
        except Exception:
            traceback.print_exc()
        self._actualizar_ui_reproductor()

    @mainthread
    def _al_terminar_pista(self):
        """Callback (ya reencolado en el hilo de Kivy, ver
        _EscuchadorFinDePista) de que el tema actual termino solo, de
        principio a fin -- esto es lo que hace que sea un "reproductor
        CONTINUO": en vez de quedarse parado, pasa sola a la siguiente."""
        self._siguiente_pista(desde_fin_natural=True)

    def _pista_anterior(self):
        self._mover_pista(-1)

    def _siguiente_pista(self, desde_fin_natural=False):
        self._mover_pista(1, parar_si_no_hay_mas=desde_fin_natural)

    def _mover_pista(self, delta, parar_si_no_hay_mas=False):
        """Avanza/retrocede dentro de self._archivos_actuales (el mismo
        orden que se ve en "Mis descargas", mas nuevo primero). Si no hay
        nada sonando todavia, +1/-1 arranca desde la primera/ultima."""
        ids = [a[0] for a in self._archivos_actuales]
        if not ids:
            return
        if self._id_reproduciendo in ids:
            indice = ids.index(self._id_reproduciendo) + delta
        else:
            indice = 0 if delta > 0 else len(ids) - 1
        if indice < 0 or indice >= len(ids):
            if parar_si_no_hay_mas:
                self._detener_reproduccion()
            return
        self._iniciar_reproduccion(ids[indice])

    def _liberar_reproductor_actual(self):
        if self._reproductor is not None:
            try:
                self._reproductor.release()
            except Exception:
                traceback.print_exc()
            self._reproductor = None

    def _detener_reproduccion(self):
        """Para la reproduccion por completo (no es "pausa"): suelta el
        MediaPlayer, limpia el estado y oculta la barra de abajo. Se usa
        al cerrar la app, al borrar el tema que esta sonando, o al llegar
        al final de la lista en modo "continuo"."""
        self._liberar_reproductor_actual()
        id_anterior = self._id_reproduciendo
        self._id_reproduciendo = None
        self._pausado = False
        self._mostrar_barra_reproductor(False)
        if id_anterior is not None:
            tarjeta = self._tarjetas_por_id.get(id_anterior)
            if tarjeta is not None:
                tarjeta.set_en_reproduccion(False)

    @mainthread
    def _mostrar_barra_reproductor(self, visible):
        self.barra_reproductor.opacity = 1 if visible else 0
        self.barra_reproductor.height = dp(52) if visible else 0

    @mainthread
    def _actualizar_ui_reproductor(self):
        """Pinta la tarjeta correcta como "sonando" (y despinta cualquier
        otra) y pone el glifo correcto (► / ❙❙) tanto en esa tarjeta como
        en la barra de abajo."""
        for id_fila, tarjeta in self._tarjetas_por_id.items():
            tarjeta.set_en_reproduccion(id_fila == self._id_reproduciendo, self._pausado)
        self.boton_play_pausa_barra.text = "▶" if self._pausado else "❙❙"

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
    #   2) Agregar metadatos (FFmpegMetadata) + incrustar caratula (a mano,
    #      con ffmpeg directo -- ver _incrustar_caratula), por separado y a
    #      "mejor esfuerzo": si este paso falla por lo que sea, el mp3 del
    #      paso 1 NO se pierde ni se borra -- la fila queda en "listo"
    #      igual (con un aviso), en vez de mostrar un error y no dejar
    #      ningun archivo.

    def _worker(self):
        while True:
            link, fila = self.cola.get()
            try:
                link_descarga, caratula_forzada = self._resolver_link(link, fila)
                fila.set_estado("PREPARANDO", COLOR_INFO)
                opciones = self._opciones_descarga(fila)
                with yt_dlp.YoutubeDL(opciones) as ydl:
                    info = ydl.extract_info(link_descarga, download=True)
                    info = self._desenvolver_resultado_busqueda(info)
                    archivo = self._ruta_mp3_final(ydl, info)

                if caratula_forzada:
                    # Es un tema que vino de Spotify: la caratula real del
                    # tema (la de Spotify) es mejor que la miniatura del
                    # video de YouTube que matcheo la busqueda -- se
                    # reemplaza ANTES de _agregar_metadatos_y_caratula,
                    # que busca la miniatura en disco por nombre sin saber
                    # ni importarle de donde salio.
                    self._forzar_caratula(archivo, caratula_forzada)

                try:
                    fila.set_estado("PROCESANDO", COLOR_INFO, detalle="agregando metadatos y caratula...")
                    self._agregar_metadatos_y_caratula(archivo, info)
                except Exception as e2:
                    # El audio ya esta guardado (paso 1 OK) -- esto solo
                    # afecta metadatos/caratula, no es un error fatal.
                    fila.set_estado("PROCESANDO", COLOR_ADVERTENCIA, detalle=f"sin caratula/metadatos: {self._resumen_error(e2)}")

                # A proposito SIN try/except propio: si publicar en
                # Descargas falla, el usuario NO tiene el tema en el
                # telefono (el archivo de trabajo queda en una carpeta
                # privada que no puede abrir), asi que eso es un fallo
                # real y tiene que verse como tal -- cae al "ERROR" del
                # except de abajo (chip rojo), nunca como un "LISTO" con
                # matices que se puedan confundir con un exito.
                self._publicar_en_musica(archivo)
                self._limpiar_temporales(archivo)
                fila.set_estado("LISTO", COLOR_OK)
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

    @staticmethod
    def _desenvolver_resultado_busqueda(info):
        """"ytsearchN:consulta" (lo que usa _resolver_link para los links
        de Spotify) SIEMPRE devuelve un resultado de tipo "playlist" --
        incluso pidiendo un solo resultado (ytsearch1:) -- con el video
        real adentro de info["entries"][0]; esto es asi en TODAS las
        versiones de yt-dlp, por como esta armada la clase base que usan
        internamente todos los buscadores (SearchInfoExtractor,
        extractor/common.py: siempre llama a self.playlist_result(...)).

        El diccionario de nivel superior (el "playlist") trae el texto de
        la BUSQUEDA como titulo (no el titulo real del video que
        matcheo), asi que _ruta_mp3_final -- que reconstruye el nombre de
        archivo a partir del titulo cuando "filepath" no esta disponible
        -- construye un nombre que casi nunca coincide con el que yt-dlp
        realmente escribio en disco (ese se arma con el titulo REAL del
        video). Confirmado reproduciendo el error exacto que vio Edgar
        ("no se encontro el mp3 convertido") con una descarga real de
        prueba, y confirmando que se arregla desenvolviendo "entries"
        antes de buscar el archivo.

        Si no es un resultado de busqueda (info.get("entries") es None,
        o sea un link de YouTube pegado directo), devuelve info tal
        cual, sin tocar nada."""
        entradas = info.get("entries")
        if entradas is None:
            return info
        entradas = list(entradas)
        if not entradas:
            raise RuntimeError("no se encontro ningun resultado en YouTube para esa busqueda")
        return entradas[0]

    @staticmethod
    def _ruta_mp3_final(ydl, info):
        """Encuentra la ruta real del mp3 que dejo el paso 1 (descarga +
        FFmpegExtractAudio). info.get("filepath")/info.get("_filename")
        a veces viene vacio (NoneType) segun el video -- esa fue la causa
        real de "expected str, bytes or os.PathLike object, not
        NoneType" que vio Edgar: ese None llegaba intacto hasta
        os.path.* en _publicar_en_musica/_limpiar_temporales y explotaba
        ahi. Por eso primero se valida que el campo realmente apunte a
        un archivo que existe, y si no, se reconstruye el nombre a mano
        a partir de la plantilla de salida (outtmpl) cambiando la
        extension a .mp3, que es siempre lo que deja FFmpegExtractAudio
        en el disco."""
        candidato = info.get("filepath") or info.get("_filename")
        if candidato and os.path.isfile(candidato):
            return candidato

        base = ydl.prepare_filename(info)
        candidato = os.path.splitext(base)[0] + ".mp3"
        if os.path.isfile(candidato):
            return candidato

        raise FileNotFoundError(f"no se encontro el mp3 convertido (se probo: {candidato!r})")

    # --- Soporte para links de Spotify ----------------------------------
    #
    # Spotify protege el audio de sus temas (eso es lo que pide un ARL de
    # cuenta de Deezer para saltear, algo que esta app NO hace -- ver
    # conversacion). Lo que SI se puede hacer sin romper ninguna
    # proteccion: leer el nombre del tema + artista + caratula de la
    # pagina PUBLICA de Spotify (nadie necesita loguearse para ver esos
    # datos, es la misma info que muestra cualquier preview de link), y
    # con eso buscar y bajar el AUDIO desde YouTube -- exactamente el
    # mismo motor que ya usa el resto de la app.

    _PATRON_SPOTIFY = re.compile(r"spotify:track:([A-Za-z0-9]+)|open\.spotify\.com/(?:intl-\w+/)?track/([A-Za-z0-9]+)")

    @classmethod
    def _es_link_spotify(cls, link):
        return cls._PATRON_SPOTIFY.search(link) is not None

    def _resolver_link(self, link, fila):
        """Si "link" es de Spotify, busca el tema + artista + caratula
        (datos publicos, sin login) y devuelve en su lugar el link
        DIRECTO del video de YouTube que mejor matchea -- asi el resto
        del codigo (descarga, conversion, metadatos) no se entera de
        que esto empezo en Spotify, es exactamente el mismo camino que
        un link de YouTube pegado a mano. Si no es de Spotify, devuelve
        el link tal cual.

        Devuelve (link_para_yt_dlp, url_caratula_o_None)."""
        if not self._es_link_spotify(link):
            return link, None

        fila.set_estado("PREPARANDO", COLOR_INFO, detalle="leyendo el tema de Spotify...")
        titulo, artista, caratula, error = self._metadata_spotify(link)
        if not titulo:
            detalle = f": {error}" if error else ""
            raise RuntimeError(f"no se pudo leer el tema de Spotify (revisa el link){detalle}")

        consulta = f"{artista} - {titulo}" if artista else titulo
        fila.set_estado("PREPARANDO", COLOR_INFO, detalle=f'Spotify -> buscando "{consulta}" en YouTube...')
        url_youtube = self._mejor_resultado_youtube(consulta, artista, titulo)
        return url_youtube, caratula

    _CANTIDAD_RESULTADOS_BUSQUEDA = 5

    @classmethod
    def _mejor_resultado_youtube(cls, consulta, artista, titulo):
        """El primer resultado de una busqueda de YouTube NO siempre es
        el tema correcto -- un cover de otro interprete, una version en
        vivo, un remix o un video con titulo parecido pueden rankear
        mas arriba que el audio real del tema buscado (esto le paso a
        Edgar: busco un tema y bajo uno de otro interprete, con la
        caratula de Spotify del tema que SI habia elegido puesta
        encima). Por eso ac se piden varios candidatos y se elige el
        que mejor matchea el interprete + tema (+ version, si el
        titulo de Spotify la incluye -- va adentro de "titulo" tal
        cual vino, remix/live/etc. incluido) -- en vez de confiar en el
        orden de YouTube.

        Solo se piden METADATOS de los candidatos (download=False); el
        audio se descarga una sola vez, recien despues, del elegido --
        confirmado leyendo YoutubeDL.process_ie_result (el flag
        download se pasa intacto a cada entrada resuelta) que esto no
        descarga nada de mas.

        Devuelve la URL del video elegido (link normal de YouTube,
        igual a uno pegado a mano)."""
        opciones = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }
        with yt_dlp.YoutubeDL(opciones) as ydl:
            info = ydl.extract_info(
                f"ytsearch{cls._CANTIDAD_RESULTADOS_BUSQUEDA}:{consulta}", download=False)

        candidatos = list((info or {}).get("entries") or [])
        if not candidatos:
            raise RuntimeError(f'no se encontro nada en YouTube para "{consulta}"')

        mejor = max(candidatos, key=lambda c: cls._puntaje_resultado(artista, titulo, c))
        return (mejor.get("webpage_url") or mejor.get("url")
                or f"https://www.youtube.com/watch?v={mejor.get('id')}")

    @staticmethod
    def _normalizar_texto(texto):
        """minusculas, sin acentos, sin signos -- para comparar titulos
        sin que un acento de mas/de menos cambie el resultado."""
        texto = (texto or "").lower()
        texto = unicodedata.normalize("NFKD", texto)
        texto = "".join(c for c in texto if not unicodedata.combining(c))
        texto = re.sub(r"[^a-z0-9 ]", " ", texto)
        return re.sub(r"\s+", " ", texto).strip()

    @classmethod
    def _puntaje_resultado(cls, artista, titulo, entrada):
        """Que tan bien matchea un resultado de YouTube con el
        interprete + tema reales de Spotify. Combina similitud de
        texto completa (titulo del video contra "interprete - tema",
        asi que una version/remix/live que Spotify incluya en el
        titulo tambien suma o resta similitud) con un plus fuerte si
        el CANAL del video es el del interprete -- asi es como YouTube
        identifica los uploads oficiales de un artista (canales
        "Interprete - Topic" generados para su discografia, o su canal
        verificado), la señal mas confiable de que es el tema correcto
        y no un cover/version de otro interprete con titulo parecido."""
        consulta_norm = cls._normalizar_texto(f"{artista} - {titulo}" if artista else titulo)
        titulo_video_norm = cls._normalizar_texto(entrada.get("title"))
        canal_norm = cls._normalizar_texto(entrada.get("channel") or entrada.get("uploader") or "")

        puntaje = difflib.SequenceMatcher(None, consulta_norm, titulo_video_norm).ratio()

        artista_norm = cls._normalizar_texto(artista) if artista else ""
        if artista_norm and artista_norm in canal_norm:
            puntaje += 0.35
        elif artista_norm and artista_norm in titulo_video_norm:
            puntaje += 0.15

        titulo_norm = cls._normalizar_texto(titulo)
        if titulo_norm and titulo_norm in titulo_video_norm:
            puntaje += 0.15

        return puntaje

    @staticmethod
    def _metadata_spotify(link):
        """Titulo, artista y caratula de un tema de Spotify, usando SOLO
        paginas publicas (nada de login ni API key):

        1) El oEmbed oficial de Spotify (documentado en
           developer.spotify.com/documentation/embeds/reference/oembed),
           que da "title" y "thumbnail_url" siempre.
        2) La pagina normal del tema (tambien publica), de la que se
           saca el ARTISTA por separado -- el <title> de esa pagina
           sigue el patron "Tema - song by Artista | Spotify". Si este
           paso falla (p.ej. Spotify cambia el formato de la pagina), no
           es grave: se sigue con el titulo solo (sin artista), la
           busqueda en YouTube es un poco menos precisa pero igual
           funciona.

        Devuelve (titulo, artista_o_None, url_caratula_o_None,
        error_o_None); titulo es None si ni siquiera el oEmbed respondio
        (en ese caso error trae el detalle real de por que, para poder
        mostrarlo en vez de un mensaje generico)."""
        titulo, artista, caratula = None, None, None
        try:
            url_oembed = "https://open.spotify.com/oembed?url=" + urllib.parse.quote(link, safe="")
            peticion = urllib.request.Request(url_oembed, headers=_CABECERAS_HTTP)
            with urllib.request.urlopen(peticion, timeout=15, context=_CONTEXTO_SSL) as resp:
                datos = json.loads(resp.read().decode("utf-8"))
            titulo = datos.get("title") or None
            caratula = datos.get("thumbnail_url") or None
        except Exception as e:
            traceback.print_exc()
            return None, None, None, f"{type(e).__name__}: {e}"

        try:
            peticion = urllib.request.Request(link, headers=_CABECERAS_HTTP)
            with urllib.request.urlopen(peticion, timeout=15, context=_CONTEXTO_SSL) as resp:
                html = resp.read().decode("utf-8", errors="replace")
            m = re.search(r"<title>(.*?)\s*-\s*song by\s*(.*?)\s*\|\s*Spotify</title>", html, re.IGNORECASE)
            if m:
                titulo, artista = m.group(1).strip(), m.group(2).strip()
            if not caratula:
                m2 = re.search(r'<meta property="og:image" content="([^"]+)"', html)
                if m2:
                    caratula = m2.group(1)
        except Exception:
            traceback.print_exc()  # a mejor esfuerzo -- ya tenemos el titulo del oEmbed

        return titulo, artista, caratula, None

    @staticmethod
    def _forzar_caratula(ruta_mp3, url_caratula):
        """Descarga la caratula OFICIAL del tema de Spotify y la deja en
        disco con el mismo nombre base que el mp3 -- asi
        _incrustar_caratula (que busca por nombre, sin saber de donde
        salio el audio) la encuentra a ELLA en vez de a la miniatura del
        video de YouTube que matcheo la busqueda.

        A PROPOSITO se descarga ANTES de borrar nada: si bajar la
        caratula de Spotify falla (red, url rota, lo que sea), la
        miniatura de YouTube que ya habia quedado escrita (de
        writethumbnail=True) se deja intacta como respaldo, en vez de
        quedar la descarga sin ninguna caratula por una falla que no
        tiene nada que ver con YouTube."""
        carpeta = os.path.dirname(ruta_mp3)
        base = os.path.splitext(os.path.basename(ruta_mp3))[0]
        destino = os.path.join(carpeta, base + ".jpg")
        try:
            peticion = urllib.request.Request(url_caratula, headers=_CABECERAS_HTTP)
            with urllib.request.urlopen(peticion, timeout=15, context=_CONTEXTO_SSL) as resp:
                contenido = resp.read()
        except Exception:
            traceback.print_exc()  # a mejor esfuerzo -- se sigue con la miniatura
            return                  # de YouTube que ya estaba en disco (si habia)

        # Recien aca, con la caratula de Spotify ya en mano, se limpia
        # cualquier miniatura vieja con el mismo nombre base para que
        # _incrustar_caratula no encuentre dos candidatas.
        for extension in (".webp", ".jpg", ".jpeg", ".png"):
            viejo = os.path.join(carpeta, base + extension)
            if os.path.isfile(viejo) and viejo != destino:
                try:
                    os.remove(viejo)
                except OSError:
                    pass
        with open(destino, "wb") as f:
            f.write(contenido)

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
        """Paso 2 (a mejor esfuerzo): metadatos (via yt-dlp) + caratula (a
        mano, con ffmpeg directo -- ver _incrustar_caratula).

        Por que la caratula NO se hace via el postprocesador
        EmbedThumbnail de yt-dlp (como se intento antes, build #19): esa
        clase depende de que info['thumbnails'][idx]['filepath'] siga
        apuntando, en esta MISMA instancia nueva de YoutubeDL, al archivo
        que efectivamente escribio en disco la instancia ANTERIOR que
        hizo la descarga -- una propagacion de estado entre dos objetos
        yt-dlp distintos, via un dict interno (__files_to_move) que ni
        siquiera es parte de la API publica de la libreria. Probarlo
        requeriria un video real de YouTube, y este entorno no tiene
        salida a internet para confirmarlo con certeza.

        Por eso la caratula se maneja ENTERAMENTE aparte, sin depender
        de nada de eso: _incrustar_caratula busca la miniatura
        DIRECTAMENTE EN EL DISCO (incluso tiene que estar ahi, a este
        punto el paso 1 ya la escribio -- writethumbnail=True) y la
        incrusta con una llamada propia a ffmpeg. Lo unico de lo que
        depende es de que el archivo este donde yt-dlp siempre lo deja
        (al lado del mp3, mismo nombre base) -- eso si se puede
        confirmar sin salir a internet, y se confirmo leyendo el codigo
        fuente real de yt-dlp instalado en este entorno."""
        opciones = {
            "quiet": True,
            "no_warnings": True,
            "ffmpeg_location": ffmpeg_path(),
            "postprocessors": [
                {"key": "FFmpegMetadata"},
            ],
        }
        with yt_dlp.YoutubeDL(opciones) as ydl:
            ydl.post_process(archivo, info)
        if not self._incrustar_caratula(archivo):
            raise RuntimeError("no se encontro o no se pudo incrustar ninguna caratula")

    @staticmethod
    def _tipo_imagen_real(ruta):
        """Que tipo de imagen es un archivo, mirando sus primeros bytes --
        NO su extension. Hace falta: YouTube a veces entrega una
        miniatura con bytes webp detras de una extension que parece jpg
        (bug conocido, el propio yt-dlp tiene un parche para esto mismo:
        EmbedThumbnailPP.fixup_webp). Devuelve "jpg", "png", "webp" o
        None (no reconocido / no se pudo leer)."""
        try:
            with open(ruta, "rb") as f:
                cabecera = f.read(16)
        except OSError:
            return None
        if cabecera.startswith(b"\xff\xd8\xff"):
            return "jpg"
        if cabecera.startswith(b"\x89PNG\r\n\x1a\n"):
            return "png"
        if cabecera[:4] == b"RIFF" and cabecera[8:12] == b"WEBP":
            return "webp"
        return None

    @classmethod
    def _incrustar_caratula(cls, ruta_mp3):
        """Busca en el disco, al lado del mp3 (mismo nombre base -- asi
        la deja escrita yt-dlp con writethumbnail=True), la miniatura
        del Paso 1 y la incrusta en el mp3 con una llamada directa a
        ffmpeg -- el mismo comando que usa EmbedThumbnail de yt-dlp por
        adentro, pero manejado aca a mano para no depender de su
        bookkeeping interno (ver docstring de _agregar_metadatos_y_caratula).

        Nuestro ffmpeg compilado para Android no tiene codificador PNG
        (solo el contenedor APNG, que es otra cosa -- confirmado
        revisando el binario con 'strings'), asi que si la miniatura no
        es ya jpg/png DE VERDAD (ver _tipo_imagen_real), se convierte
        primero a jpg (codificador mjpeg, ese si esta).

        Devuelve True si quedo incrustada, False si no habia miniatura o
        algo fallo (a mejor esfuerzo -- quien llama decide que hacer)."""
        carpeta = os.path.dirname(ruta_mp3)
        base = os.path.splitext(os.path.basename(ruta_mp3))[0]
        miniatura = None
        for extension in (".webp", ".jpg", ".jpeg", ".png"):
            candidato = os.path.join(carpeta, base + extension)
            if os.path.isfile(candidato):
                miniatura = candidato
                break
        if miniatura is None:
            print("[caratula] no se encontro ninguna miniatura en disco")
            return False

        if cls._tipo_imagen_real(miniatura) not in ("jpg", "png"):
            convertida = os.path.join(carpeta, base + "_caratula.jpg")
            try:
                resultado = subprocess.run(
                    [
                        ffmpeg_path() or "ffmpeg",
                        "-y", "-loglevel", "error",
                        "-i", miniatura,
                        "-update", "1", "-f", "image2", "-pattern_type", "none",
                        convertida,
                    ],
                    capture_output=True,
                )
            except Exception:
                traceback.print_exc()
                return False
            if resultado.returncode != 0 or not os.path.isfile(convertida):
                print(f"[caratula] fallo la conversion a jpg: {resultado.stderr!r}")
                return False
            try:
                os.remove(miniatura)
            except OSError:
                pass
            miniatura = convertida

        temporal = os.path.join(carpeta, base + "_con_caratula.mp3")
        try:
            resultado = subprocess.run(
                [
                    ffmpeg_path() or "ffmpeg",
                    "-y", "-loglevel", "error",
                    "-i", ruta_mp3, "-i", miniatura,
                    "-map", "0:0", "-map", "1:0", "-c", "copy",
                    "-write_id3v1", "1", "-id3v2_version", "3",
                    "-metadata:s:v", "title=Album cover",
                    "-metadata:s:v", "comment=Cover (front)",
                    temporal,
                ],
                capture_output=True,
            )
        except Exception:
            traceback.print_exc()
            return False
        if resultado.returncode == 0 and os.path.isfile(temporal):
            os.replace(temporal, ruta_mp3)
            try:
                os.remove(miniatura)
            except OSError:
                pass
            return True

        print(f"[caratula] fallo al incrustar: {resultado.stderr!r}")
        try:
            os.remove(temporal)
        except OSError:
            pass
        return False

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
        # ContentValues.put() tiene MUCHAS firmas sobrecargadas que aceptan un
        # numero (Integer, Long, Short, Byte, Float, Double...). Pasarle un
        # int de Python asi nomas (valores.put("is_pending", 1)) es AMBIGUO
        # para pyjnius -- no sabe a cual firma Java llamar y tira un error de
        # "ambiguous overload" (el error en rojo que vio Edgar en el build
        # anterior). Forzando el boxeo a java.lang.Integer explicitamente se
        # elimina la ambiguedad: solo matchea una firma posible.
        Integer = autoclass("java.lang.Integer")
        activity = PythonActivity.mActivity
        resolver = activity.getContentResolver()

        nombre_archivo = os.path.basename(ruta_archivo)

        valores = ContentValues()
        valores.put("_display_name", nombre_archivo)
        valores.put("mime_type", "audio/mpeg")
        valores.put("relative_path", RUTA_RELATIVA_PUBLICA)
        valores.put("is_pending", Integer(1))

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
        valores_final.put("is_pending", Integer(0))
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

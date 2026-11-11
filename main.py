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
  .apk como "libffmpeg.so" -- el nombre rarp es a proposito, ver el
  docstring de ese recipe para la explicacion completa (resumen: es la unica
  forma de que Android deje EJECUTAR un binario desde Android 10 en
  adelante). yt-dlp lo invoca con --ffmpeg-location exactamente como en
  Windows, via subprocess, sin que el codigo de descarga sepa ni le importe
  la diferencia.

Los archivos finales quedan en una carpeta propia de la app (no hace falta
pedir permisos de almacenamiento en ningun Android): se puede ver la ruta
exacta abajo de todo en la pantalla de la app, o con cualquier explorador
de archivos navegando a Android/data/<paquete>/files/Descargas.
"""
import os
import queue
import threading

from kivy.app import App
from kivy.clock import mainthread
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.progressbar import ProgressBar
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput

import yt_dlp


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


class FilaDescarga(BoxLayout):
    """Una fila de la lista: el link, una barra de progreso y un mensaje de
    estado -- el mismo concepto que la cola de MediaVault en Windows."""

    def __init__(self, url, **kwargs):
        super().__init__(orientation="vertical", size_hint_y=None, height=72, padding=(10, 6), spacing=4, **kwargs)
        self.label_url = Label(
            text=url, size_hint_y=None, height=24, halign="left", valign="middle", shorten=True, shorten_from="right"
        )
        self.label_url.bind(size=lambda inst, val: setattr(inst, "text_size", val))
        self.barra = ProgressBar(max=100, value=0, size_hint_y=None, height=10)
        self.label_estado = Label(
            text="en cola...", size_hint_y=None, height=20, halign="left", valign="middle",
            color=(0.7, 0.7, 0.7, 1), font_size=12,
        )
        self.label_estado.bind(size=lambda inst, val: setattr(inst, "text_size", val))
        self.add_widget(self.label_url)
        self.add_widget(self.barra)
        self.add_widget(self.label_estado)

    @mainthread
    def set_progreso(self, valor):
        self.barra.value = valor

    @mainthread
    def set_mensaje(self, texto):
        self.label_estado.text = texto


class TubeSyncMobileApp(App):
    title = "TubeSync Mobile"

    def build(self):
        self.cola = queue.Queue()
        self.carpeta_destino = carpeta_descargas()

        raiz = BoxLayout(orientation="vertical", padding=14, spacing=10)

        self.input_links = TextInput(
            hint_text="Pega uno o mas links de YouTube (uno por linea)",
            size_hint_y=0.28,
            multiline=True,
        )
        raiz.add_widget(self.input_links)

        boton = Button(text="Descargar audio (MP3 320kbps)", size_hint_y=None, height=50)
        boton.bind(on_release=self.agregar_links)
        raiz.add_widget(boton)

        self.lista = GridLayout(cols=1, size_hint_y=None, spacing=8)
        self.lista.bind(minimum_height=self.lista.setter("height"))
        scroll = ScrollView()
        scroll.add_widget(self.lista)
        raiz.add_widget(scroll)

        raiz.add_widget(
            Label(
                text=f"Se guardan en: {self.carpeta_destino}",
                size_hint_y=None,
                height=40,
                font_size=11,
                color=(0.6, 0.6, 0.6, 1),
                halign="left",
                valign="middle",
                text_size=(None, None),
            )
        )

        threading.Thread(target=self._worker, daemon=True).start()
        return raiz

    def agregar_links(self, *_):
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
                fila.set_mensaje("listo")
            except Exception as e:
                fila.set_progreso(0)
                fila.set_mensaje(f"error: {e}")
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

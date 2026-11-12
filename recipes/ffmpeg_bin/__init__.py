"""Recipe de python-for-android para empaquetar ffmpeg en la app Android.

Este recipe NO compila ffmpeg -- solo lo COPIA. El binario real (ya
compilado para arm64-v8a) lo deja ahi, al lado de este archivo
(ffmpeg-arm64-v8a), un paso ANTERIOR del workflow de GitHub Actions
("Compilar ffmpeg con soporte MP3 (libmp3lame)", en build-apk.yml), asi que
ese archivo NO esta commiteado en git -- se genera en cada corrida (con
cache, ver el workflow) justo antes de "buildozer android debug".

Por que se compila ffmpeg desde cero en vez de usar un binario ya hecho de
internet (como se hacia antes, con https://github.com/hzw1199/Android-FFmpeg-Prebuilt):
ese binario prearmado estaba compilado con `--disable-gpl`, lo que significa
que NO tenia ningun codificador de MP3 (el unico codificador de MP3 que
existe, libmp3lame, requiere `--enable-gpl`) -- por eso las descargas
fallaban con "Encoder not found" aunque todo lo demas (certificados,
sys.stdout/stderr, etc.) ya estaba arreglado. La solucion es compilar
nuestro propio ffmpeg con libmp3lame incluido (ver THIRD_PARTY_LICENSES/
en la raiz del repo para la licencia -- este build es GPL v2+, no LGPL).

El binario resultante es igual de "real" que el anterior: compilado con el
NDK de Android (interprete /system/bin/linker64, enlazado contra las
librerias propias de Android: libc.so, libm.so, etc. -- NO es un binario de
Linux de escritorio, es un ejecutable real pensado para correr en el
telefono).

Por que se llama "libffmpeg.so" en vez de "ffmpeg"
---------------------------------------------------
Desde Android 10, un archivo que la app escriba en tiempo de ejecucion NO
se puede ejecutar (politica W^X: ninguna carpeta donde la app pueda escribir
permite tambien ejecutar). La UNICA carpeta que SI tiene permiso de
ejecucion es "nativeLibraryDir", y esa carpeta Android la llena
automaticamente al INSTALAR el .apk, copiando ahi los archivos
"lib*.so" que vinieron empaquetados dentro de jniLibs/<arquitectura>/.

Entonces el truco (usado por varias apps de Android que necesitan empaquetar
un binario real, no solo una libreria) es: renombrar el ejecutable para que
cumpla el patron "lib*.so", meterlo en jniLibs/arm64-v8a/ al compilar la
app, y en tiempo de ejecucion pedirle a Android la ruta de
nativeLibraryDir (ver ffmpeg_path() en main.py) -- ahi SI se puede invocar
como el binario real que es. yt-dlp lo usa despues exactamente igual que a
ffmpeg.exe en la version de Windows (via --ffmpeg-location + subprocess),
sin que le importe el nombre raro del archivo.

self.ctx.get_libs_dir(arch.arch) es el mecanismo oficial y documentado de
python-for-android para esto: lo que se copia ahi queda incluido
automaticamente en el jniLibs del proyecto Android que python-for-android
genera (ver https://python-for-android.readthedocs.io/en/latest/recipes.html).
"""
import os

from pythonforandroid.recipe import Recipe
from pythonforandroid.logger import shprint
import sh

_AQUI = os.path.dirname(os.path.abspath(__file__))


class FfmpegBinRecipe(Recipe):
    version = "7.1-lame3.100"
    url = None  # no hay nada que descargar aca -- ver el workflow de CI

    def should_build(self, arch):
        # Es una simple copia de archivo -- siempre rapido, siempre se re-hace.
        return True

    def build_arch(self, arch):
        origen = os.path.join(_AQUI, "ffmpeg-arm64-v8a")
        if not os.path.isfile(origen):
            raise FileNotFoundError(
                f"No esta el binario de ffmpeg compilado en {origen}. "
                "Este archivo lo tiene que generar el paso 'Compilar ffmpeg "
                "con soporte MP3 (libmp3lame)' del workflow de GitHub "
                "Actions ANTES de 'buildozer android debug' -- no esta "
                "commiteado en git a proposito (se genera y se cachea en "
                "cada corrida)."
            )
        libs_dir = self.ctx.get_libs_dir(arch.arch)
        os.makedirs(libs_dir, exist_ok=True)
        destino = os.path.join(libs_dir, "libffmpeg.so")
        shprint(sh.cp, origen, destino)
        shprint(sh.chmod, "755", destino)


recipe = FfmpegBinRecipe()

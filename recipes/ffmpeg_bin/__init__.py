"""Recipe de python-for-android para empaquetar ffmpeg en la app Android.

Este recipe NO compila nada: el binario de ffmpeg para Android (arm64-v8a)
ya viene compilado, al lado de este archivo (ffmpeg-arm64-v8a, ~15MB). Viene
de https://github.com/hzw1199/Android-FFmpeg-Prebuilt (licencia LGPL v2.1+,
ver THIRD_PARTY_LICENSES/ en la raiz del repo), compilado con el NDK de
Android (interprete /system/bin/linker64, enlazado contra las librerias
propias de Android: libc.so, libm.so, etc. -- NO es un binario de Linux de
escritorio, es un ejecutable real pensado para correr en el telefono).

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
    version = "9.0"
    url = None  # no hay nada que descargar, el binario ya esta en este mismo repo

    def should_build(self, arch):
        # Es una simple copia de archivo -- siempre rapido, siempre se re-hace.
        return True

    def build_arch(self, arch):
        origen = os.path.join(_AQUI, "ffmpeg-arm64-v8a")
        libs_dir = self.ctx.get_libs_dir(arch.arch)
        os.makedirs(libs_dir, exist_ok=True)
        destino = os.path.join(libs_dir, "libffmpeg.so")
        shprint(sh.cp, origen, destino)
        shprint(sh.chmod, "755", destino)


recipe = FfmpegBinRecipe()

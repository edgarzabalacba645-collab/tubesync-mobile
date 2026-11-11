[app]

title = TubeSync Mobile
package.name = tubesyncmobile
package.domain = com.edgarzabala

source.dir = .
source.include_exts = py,png,jpg,kv,atlas

version = 0.1

# yt-dlp: el motor de descarga (igual que en la version de Windows).
# certifi: certificados SSL -- en Android a veces el detectado por Python
#          no alcanza, certifi asegura que las descargas por HTTPS no fallen.
# mutagen: respaldo puro-Python para metadatos/caratula si ffmpeg no puede.
# pyjnius: puente Python <-> Java, lo usa main.py para encontrar
#          nativeLibraryDir (ruta de ffmpeg) y la carpeta de descargas.
# ffmpeg_bin: recipe local (ver recipes/ffmpeg_bin/) que empaqueta el
#          binario real de ffmpeg dentro del .apk.
#
# python3==3.11.9 / hostpython3==3.11.9: fijo la version EXACTA (con
# parche -- python-for-android descarga el tag de git "vX.Y.Z" de
# cpython, y un tag como "v3.11" sin numero de parche no existe). Fijo la
# version ademas porque la mas nueva disponible (3.14, recien salida)
# todavia no tiene wheel precompilado para Android de una dependencia de
# yt-dlp (charset_normalizer). 3.11.9 es una version mucho mas usada y
# probada en python-for-android. Hay que fijar las DOS (la que corre en
# el celular y la que corre durante la compilacion) -- python-for-android
# exige que sean la misma version.
requirements = python3==3.11.9,hostpython3==3.11.9,kivy,yt-dlp,certifi,mutagen,pyjnius,ffmpeg_bin

orientation = portrait
fullscreen = 0

# Recipes propios (el de ffmpeg_bin) -- sin esto, buildozer/p4a no sabe que
# existen.
p4a.local_recipes = ./recipes

android.permissions = INTERNET
android.api = 34
android.minapi = 24
android.ndk = 25b
android.archs = arm64-v8a
android.allow_backup = True
android.accept_sdk_license = True

[buildozer]
log_level = 2
warn_on_root = 1

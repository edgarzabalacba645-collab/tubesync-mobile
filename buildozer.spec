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
# python3==3.11: fijo la version (si no, toma la mas nueva disponible --
# en un intento anterior tomo 3.14, recien salida, y una dependencia de
# yt-dlp (charset_normalizer) todavia no tiene wheel precompilado para
# Android en esa version tan nueva). 3.11 es una version mucho mas usada
# y probada en python-for-android.
requirements = python3==3.11,kivy,yt-dlp,certifi,mutagen,pyjnius,ffmpeg_bin

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

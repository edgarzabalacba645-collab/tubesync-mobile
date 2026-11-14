[app]

title = TubeSync Mobile
package.name = tubesyncmobile
package.domain = com.edgarzabala

source.dir = .
source.include_exts = py,png,jpg,kv,atlas

version = 0.1

# Icono propio y color de pantalla de carga (presplash) -- sin esto el
# instalador usa el icono generico de python-for-android y una pantalla de
# carga blanca lisa, que es justamente lo que hace que la app se sienta
# "cruda"/sin terminar al abrirla.
icon.filename = %(source.dir)s/icon.png
android.presplash_color = #121218

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

# Compilamos en modo "release" (no "debug") para poder firmar con una clave
# FIJA y explicita via las variables P4A_RELEASE_KEYSTORE/KEYALIAS/etc (ver
# build-apk.yml). El modo "debug" tambien se puede firmar con una clave
# fija en teoria (dejando un keystore fijo en ~/.android/debug.keystore),
# pero se verifico con certeza que en este entorno de CI eso NO funciona:
# se bajaron y revisaron los .apk de 4 builds reales (21 a 24) y cada uno
# tenia un certificado de firma DISTINTO, generado justo en el momento de
# cada build -- es decir, Gradle estaba ignorando el keystore fijo y
# generando uno nuevo al azar cada vez (de ahi que SIEMPRE pidiera
# desinstalar la version anterior). El modo "release" no tiene ese
# problema porque la firma no depende de ninguna convencion implicita de
# Gradle: se arma siempre con el mismo archivo exacto (ci/debug.keystore)
# pasado por variable de entorno.
# "apk" en vez del default "aab" -- un .aab no se puede instalar
# directamente en el celular, hace falta Google Play o bundletool.
android.release_artifact = apk

[buildozer]
log_level = 2
warn_on_root = 1

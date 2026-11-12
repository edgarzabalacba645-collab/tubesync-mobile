# TubeSync Mobile

App Android para bajar audio de YouTube en MP3 320kbps (con metadatos y
caratula incrustados) directo al celular. Es la version para Android de la
parte de YouTube de MediaVault/TubeSync: mismo motor (yt-dlp) y los mismos
flags de siempre, adaptados para correr dentro de una app Android en vez de
como proceso de Windows.

## Como instalar

1. Entrar a la pestaña **Releases** de este repo desde el navegador del
   celular.
2. Abrir la build mas reciente (la de arriba de todo) y descargar el
   archivo `.apk` de "Assets".
3. Abrirlo. Android va a pedir permiso para "instalar apps de origenes
   desconocidos" la primera vez -- hay que aceptarlo solo para este
   archivo.

No hace falta PC ni cable: todo el build se genera solo con GitHub Actions
cada vez que se sube un cambio a este repo.

## Donde quedan los archivos descargados

En una carpeta propia de la app (no pide permisos de almacenamiento en
ningun Android): `Android/data/com.edgarzabala.tubesyncmobile/files/Descargas/`,
visible con cualquier explorador de archivos. La app tambien muestra la
ruta completa abajo de todo en la pantalla.

## Por que esto no es exactamente lo mismo que MediaVault en Windows

- **El motor es el mismo** (yt-dlp, los mismos flags: mp3 320kbps, metadatos
  y caratula incrustados), pero en vez de invocarlo como un .exe aparte, la
  app Android usa el PAQUETE yt-dlp (pip) directamente como libreria de
  Python. Es inevitable: Android no ejecuta archivos .exe.
- **ffmpeg SI es un binario real** (como ffmpeg.exe), pero compilado para
  Android y empaquetado con un truco especifico para que Android lo deje
  ejecutar (ver el docstring completo en `recipes/ffmpeg_bin/__init__.py`).
  El binario se compila desde las fuentes oficiales de ffmpeg.org en cada
  build de GitHub Actions (con libmp3lame estatico adentro, asi puede
  codificar MP3 -- ver `THIRD_PARTY_LICENSES/ffmpeg-GPL-LICENSE.md` para la
  licencia, GPL v2+ por el `--enable-gpl` que requiere libmp3lame).
- Por ahora **solo audio** (no video) -- es el uso real que se le va a dar
  desde el celular.
- Por ahora no tiene el boton de "actualizar yt-dlp" que tiene la version de
  Windows: si YouTube rompe algo, hay que subir un cambio al repo que suba
  la version del paquete `yt-dlp` en `buildozer.spec` y esperar el build
  automatico de vuelta.

## Estructura del proyecto

- `main.py` -- la app (Kivy): pegar links, cola de descargas, progreso.
- `buildozer.spec` -- configuracion del build Android (dependencias,
  permisos, arquitectura).
- `recipes/ffmpeg_bin/` -- el "recipe" que empaqueta el binario de ffmpeg
  dentro del .apk, y el binario en si (`ffmpeg-arm64-v8a`).
- `.github/workflows/build-apk.yml` -- el build automatico: compila con
  Buildozer y publica el `.apk` en una Release nueva en cada push.

#!/usr/bin/env python3
"""Diagnostico puntual (NO es parte de la app): pide la pagina publica de
un link de Spotify con el MISMO User-Agent/headers que usa main.py
(_CABECERAS_HTTP) y prueba ahi mismo los regex reales de
_metadata_spotify, para ver con certeza que esta devolviendo Spotify de
verdad -- en vez de adivinar por que _metadata_spotify no encontro el
artista en el telefono real de Edgar, cuando el mismo link SI traia el
artista via otra herramienta de fetch (asi que hay que confirmar si el
problema es el contenido real que ve la app, o el regex)."""
import html
import re
import ssl
import sys
import urllib.request

CABECERAS_HTTP = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
}

REGEX_TITULO_NUEVO = re.compile(
    r"<title>(.*?)\s*-\s*song\b.*?\bby\s*(.*?)\s*\|\s*Spotify</title>", re.IGNORECASE)
REGEX_TITULO_VIEJO = re.compile(
    r"<title>(.*?)\s*-\s*song by\s*(.*?)\s*\|\s*Spotify</title>", re.IGNORECASE)
REGEX_MUSICIAN_DESC = re.compile(
    r'<meta property="music:musician_description" content="([^"]+)"')
REGEX_OG_IMAGE = re.compile(r'<meta property="og:image" content="([^"]+)"')
REGEX_TITLE_TAG_CRUDO = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def main(link):
    contexto = ssl.create_default_context()
    peticion = urllib.request.Request(link, headers=CABECERAS_HTTP)
    with urllib.request.urlopen(peticion, timeout=20, context=contexto) as resp:
        status = resp.status
        url_final = resp.geturl()
        html_pagina = resp.read().decode("utf-8", errors="replace")

    print(f"HTTP status: {status}")
    print(f"URL final (tras redirects): {url_final}")
    print(f"Tamano del HTML recibido: {len(html_pagina)} caracteres")
    print()

    m_crudo = REGEX_TITLE_TAG_CRUDO.search(html_pagina)
    print("=== <title> CRUDO tal cual vino ===")
    print(repr(m_crudo.group(1)) if m_crudo else "(no se encontro NINGUN <title> en el HTML)")
    print()

    m_nuevo = REGEX_TITULO_NUEVO.search(html_pagina)
    print("=== Regex NUEVO (tolerante a 'song ... by') ===")
    if m_nuevo:
        print(f"titulo={m_nuevo.group(1).strip()!r} artista={html.unescape(m_nuevo.group(2)).strip()!r}")
    else:
        print("NO matcheo")
    print()

    m_viejo = REGEX_TITULO_VIEJO.search(html_pagina)
    print("=== Regex VIEJO (solo 'song by' exacto) ===")
    print("matcheo" if m_viejo else "NO matcheo (esperado si matcheo el nuevo con 'and lyrics')")
    print()

    m_musico = REGEX_MUSICIAN_DESC.search(html_pagina)
    print("=== meta music:musician_description ===")
    print(repr(html.unescape(m_musico.group(1))) if m_musico else "(no esta presente en el HTML)")
    print()

    m_img = REGEX_OG_IMAGE.search(html_pagina)
    print("=== meta og:image ===")
    print(m_img.group(1) if m_img else "(no esta presente)")
    print()

    # Por si el <title> real no tiene NADA que ver con lo esperado (p.ej.
    # una pagina de consentimiento/cookies, un interstitial "abrir en la
    # app", un bloqueo por bot, etc.) -- mostrar un pedazo del HTML crudo
    # alrededor de donde deberia estar el <head> para verlo con los
    # propios ojos, no adivinar.
    print("=== Primeros 1500 caracteres del HTML crudo ===")
    print(html_pagina[:1500])
    print()
    print("=== Posicion (indice de caracter) de cada tag clave en el HTML ===")
    for etiqueta, patron in (
        ("</title>", "</title>"),
        ("music:musician_description", "music:musician_description"),
        ("og:description", "og:description"),
        ("og:image", "og:image"),
        ("</head>", "</head>"),
    ):
        idx = html_pagina.find(patron)
        print(f"{etiqueta}: {idx}")

    print()
    print("=== Texto crudo alrededor de 'music:musician_description' (+-120) ===")
    idx = html_pagina.find("music:musician_description")
    if idx != -1:
        print(repr(html_pagina[max(0, idx - 60):idx + 250]))
    print()
    print("=== Texto crudo alrededor de 'og:description' (+-150) ===")
    idx2 = html_pagina.find("og:description")
    if idx2 != -1:
        print(repr(html_pagina[max(0, idx2 - 30):idx2 + 250]))


if __name__ == "__main__":
    main(sys.argv[1])

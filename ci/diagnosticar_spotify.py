#!/usr/bin/env python3
"""Diagnostico puntual (NO es parte de la app): reproduce EXACTAMENTE el
pedido HTTP que hace _metadata_spotify, con el link TAL CUAL lo pega el
usuario (con sus parametros de tracking -- si=, utm_source=, context=,
etc., que son los que trae un link copiado desde la app movil de
Spotify con "Compartir -> Copiar link") para ver si ese link con
parametros se comporta distinto al link "pelado" (sin parametros)."""
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
REGEX_MUSICIAN_DESC = re.compile(
    r'<meta (?:name|property)="music:musician_description" content="([^"]+)"')
REGEX_TITLE_TAG_CRUDO = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def main(link):
    contexto = ssl.create_default_context()
    print(f"Link de entrada (tal cual): {link!r}")
    print()
    peticion = urllib.request.Request(link, headers=CABECERAS_HTTP)
    try:
        with urllib.request.urlopen(peticion, timeout=25, context=contexto) as resp:
            status = resp.status
            url_final = resp.geturl()
            cuerpo = resp.read(120_000)
    except Exception as e:
        print(f"EXCEPCION: {type(e).__name__}: {e}")
        return

    html_pagina = cuerpo.decode("utf-8", errors="replace")
    print(f"HTTP status: {status}")
    print(f"URL final (tras redirects): {url_final}")
    print(f"Tamano leido: {len(html_pagina)} caracteres")
    print()

    m_crudo = REGEX_TITLE_TAG_CRUDO.search(html_pagina)
    print("=== <title> crudo ===")
    print(repr(m_crudo.group(1)) if m_crudo else "(no se encontro NINGUN <title>)")
    print()

    m_nuevo = REGEX_TITULO_NUEVO.search(html_pagina)
    print("=== Regex de titulo/artista ===")
    if m_nuevo:
        print(f"titulo={m_nuevo.group(1).strip()!r} artista={html.unescape(m_nuevo.group(2)).strip()!r}")
    else:
        print("NO matcheo")
    print()

    m_musico = REGEX_MUSICIAN_DESC.search(html_pagina)
    print("=== meta music:musician_description ===")
    print(repr(html.unescape(m_musico.group(1))) if m_musico else "(no esta presente)")
    print()

    print("=== Primeros 1000 caracteres crudos ===")
    print(html_pagina[:1000])


if __name__ == "__main__":
    main(sys.argv[1])

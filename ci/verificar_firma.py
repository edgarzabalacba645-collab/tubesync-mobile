"""Verifica que un .apk recien compilado haya quedado firmado con el mismo
keystore fijo del repo (ci/debug.keystore).

Por que existe este script: durante varios builds (del 20 al 24) cada APK
quedaba firmado con una clave DISTINTA y al azar, pese a que ya existia un
intento de fijarla -- eso obligaba a desinstalar la app anterior en el
celular antes de poder instalar cada actualizacion. Se detecto bajando los
.apk reales de esos builds y comparando sus certificados de firma (todos
distintos entre si). La solucion (compilar en modo "release" firmando con
un keystore fijo pasado por variable de entorno, ver build-apk.yml) se
verifica sola en cada build con este script, para no depender de volver a
hacer ese chequeo a mano cada vez que se sospeche del mismo problema.

Usa androguard para leer el certificado del "APK Signing Block" (v2/v3)
del .apk -- "keytool"/"jarsigner" no sirven aca porque esos solo entienden
la firma vieja basada en archivos dentro de META-INF/, y estos .apk no la
tienen (python-for-android los genera solo con firma v2/v3).
"""
import sys

from androguard.core.apk import APK
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography import x509

# androguard usa "loguru" (no el modulo "logging" estandar) y agrega su
# propio sink a stderr al importarse -- hay que sacarlo despues de este
# import, sino inunda el log del CI con el detalle interno de cada archivo
# que analiza.
try:
    from loguru import logger as _logger
    _logger.remove()
except Exception:
    pass


def main(apk_path, keystore_path, keystore_password=b"android"):
    with open(keystore_path, "rb") as f:
        _, cert_esperado, _ = pkcs12.load_key_and_certificates(
            f.read(), keystore_password)
    fp_esperado = cert_esperado.fingerprint(hashes.SHA256()).hex(":")

    apk = APK(apk_path)
    certs = apk.get_certificates_der_v2() or apk.get_certificates_der_v3() or []
    if not certs:
        sys.exit("ERROR: el .apk no tiene ningun certificado de firma legible "
                  "(ni v2 ni v3) -- no se puede verificar la firma.")
    fp_real = x509.load_der_x509_certificate(certs[0]).fingerprint(hashes.SHA256()).hex(":")

    print(f"Esperado (ci/debug.keystore): {fp_esperado}")
    print(f"Real (apk recien armado):     {fp_real}")
    if fp_esperado != fp_real:
        sys.exit("ERROR: el .apk NO quedo firmado con ci/debug.keystore -- "
                  "las actualizaciones van a volver a pedir desinstalar.")
    print("OK: firma fija confirmada.")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

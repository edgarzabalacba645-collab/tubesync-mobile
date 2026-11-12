"""Script de un solo uso para generar icon.png (512x512). No se compila ni
se bundlea en la app -- es solo una herramienta de este repo para armar el
icono una vez. Dibuja un cuadrado oscuro con esquinas redondeadas y una nota
musical simple en el color de acento, consistente con el tema de la app."""
from PIL import Image, ImageDraw

SIZE = 512
BG = (18, 18, 24, 255)        # casi negro, igual al fondo de la app
ACCENT = (255, 82, 82, 255)   # rojo/coral de acento (botones, barra superior)

img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

# Fondo: cuadrado con esquinas redondeadas.
radio = 90
draw.rounded_rectangle([0, 0, SIZE, SIZE], radius=radio, fill=BG)

# Nota musical simple: dos cabezas ovaladas + plica + barra.
cx, cy = SIZE * 0.5, SIZE * 0.52
# Plica vertical (el "palito" de la nota).
draw.rounded_rectangle(
    [cx + 40, cy - 150, cx + 62, cy + 70], radius=10, fill=ACCENT
)
# Barra superior (conecta dos notas, estilo "corchea doble").
draw.rounded_rectangle(
    [cx - 40, cy - 170, cx + 70, cy - 130], radius=14, fill=ACCENT
)
# Cabeza de la nota (ovalo), un poco inclinada.
cabeza = Image.new("RGBA", (160, 120), (0, 0, 0, 0))
cd = ImageDraw.Draw(cabeza)
cd.ellipse([0, 0, 160, 120], fill=ACCENT)
cabeza = cabeza.rotate(-18, expand=True)
img.paste(cabeza, (int(cx - 170), int(cy - 20)), cabeza)

img.save("icon.png")
print("icon.png generado")

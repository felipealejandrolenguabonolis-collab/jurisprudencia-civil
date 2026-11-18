#!/usr/bin/env python3
"""
Actualizador diario de jurisprudencia - Sala de Casación Civil (Corte Suprema de Justicia, Colombia)

1) Detecta publicaciones nuevas de la Relatoría Civil y las deja PENDIENTES de aprobación
   (estado.json). No descarga nada todavía.
2) Tú las revisas y apruebas. Solo las aprobadas se descargan a la carpeta
   "sentencias-actualizadas de claude" y se registran en jurisprudencia_civil.csv.

Instalar: pip install requests feedparser
Uso:
  python jurisprudencia_civil.py                # busca novedades y las deja pendientes
  python jurisprudencia_civil.py --aprobar      # te muestra cada pendiente: s=aprobar, n=rechazar, Enter=dejar
  python jurisprudencia_civil.py --aprobar-todo # aprueba y descarga todas las pendientes
  python jurisprudencia_civil.py --lista        # muestra pendientes
"""
import csv, json, re, sys, time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, unquote, urlparse

import feedparser
import requests

BASE = "https://cortesuprema.gov.co"
FEEDS = [
    f"{BASE}/tag/sala-de-casacion-civil/feed/",  # etiqueta oficial "Sala de Casación Civil"
    f"{BASE}/feed/",                              # respaldo: feed general, se filtra por "civil"
]
REST = f"{BASE}/wp-json/wp/v2/posts"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; JurisprudenciaBot/1.0)"}

DIR = Path(__file__).parent
CARPETA = DIR / "sentencias-actualizadas de claude"
ESTADO = DIR / "estado.json"  # cola de aprobación
CSV_OUT = DIR / "jurisprudencia_civil.csv"

RE_SC = re.compile(r"\bSC\d{1,5}-\d{4}\b")
RE_TAGS = re.compile(r"<[^>]+>")
RE_PDF = re.compile(r'href=["\']([^"\']+?\.pdf(?:\?[^"\']*)?)["\']', re.I)


def limpiar(html: str) -> str:
    return re.sub(r"\s+", " ", RE_TAGS.sub(" ", html or "")).strip()


def nombre_seguro(texto: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", texto).strip()[:120]


def desde_feed():
    items = []
    for url in FEEDS:
        feed = feedparser.parse(url, request_headers=HEADERS)
        for e in feed.entries:
            texto = f"{e.get('title','')} {e.get('summary','')}"
            if "tag/sala-de-casacion-civil" not in url and "civil" not in texto.lower():
                continue
            items.append({
                "id": e.get("id") or e.get("link"),
                "fecha": e.get("published", ""),
                "titulo": limpiar(e.get("title", "")),
                "resumen": limpiar(e.get("summary", ""))[:500],
                "url": e.get("link", ""),
                "sentencia": ", ".join(sorted(set(RE_SC.findall(texto)))),
            })
        if items:
            break
    return items


def desde_rest():
    r = requests.get(REST, params={"search": "Sala de Casación Civil", "per_page": 50,
                                   "orderby": "date", "order": "desc"},
                     headers=HEADERS, timeout=30)
    r.raise_for_status()
    items = []
    for p in r.json():
        titulo = limpiar(p["title"]["rendered"])
        resumen = limpiar(p["excerpt"]["rendered"])
        items.append({
            "id": str(p["id"]),
            "fecha": p["date"],
            "titulo": titulo,
            "resumen": resumen[:500],
            "url": p["link"],
            "sentencia": ", ".join(sorted(set(RE_SC.findall(f"{titulo} {resumen}")))),
        })
    return items


def descargar_providencias(item) -> list[str]:
    """Busca los PDF dentro de la publicación y los guarda en CARPETA. Devuelve nombres guardados."""
    CARPETA.mkdir(parents=True, exist_ok=True)
    guardados = []
    try:
        html = requests.get(item["url"], headers=HEADERS, timeout=30).text
    except Exception as exc:
        print(f"[aviso] no abrió {item['url']}: {exc}", file=sys.stderr)
        return guardados

    enlaces = []
    for href in RE_PDF.findall(html):
        full = urljoin(item["url"], href)
        if "cortesuprema.gov.co" not in full:
            continue  # ignora PDFs de otros sitios
        if full not in enlaces:
            enlaces.append(full)

    for n, pdf_url in enumerate(enlaces, 1):
        original = Path(unquote(urlparse(pdf_url).path)).name
        prefijo = item["sentencia"].split(",")[0] if item["sentencia"] else ""
        nombre = nombre_seguro(f"{prefijo}_{original}" if prefijo and prefijo not in original else original)
        destino = CARPETA / nombre
        if destino.exists():
            guardados.append(nombre)
            continue
        try:
            r = requests.get(pdf_url, headers=HEADERS, timeout=90)
            r.raise_for_status()
            if not r.content.startswith(b"%PDF"):
                continue  # no era un PDF real
            destino.write_bytes(r.content)
            guardados.append(nombre)
            print(f"   descargado: {nombre}")
            time.sleep(1)  # cortesía con el servidor
        except Exception as exc:
            print(f"[aviso] falló {pdf_url}: {exc}", file=sys.stderr)
    return guardados


def cargar():
    return json.loads(ESTADO.read_text(encoding="utf-8")) if ESTADO.exists() else {}


def guardar(estado):
    ESTADO.write_text(json.dumps(estado, ensure_ascii=False, indent=1), encoding="utf-8")


def buscar_novedades():
    try:
        items = desde_feed()
    except Exception as exc:
        print(f"[aviso] feed falló: {exc}", file=sys.stderr)
        items = []
    if not items:
        items = desde_rest()
    estado = cargar()
    nuevos = [i for i in items if i["id"] not in estado]
    for i in nuevos:
        estado[i["id"]] = {**i, "estado": "pendiente"}
    guardar(estado)
    print(f"{len(nuevos)} novedad(es) en cola de aprobación ({len(items)} revisadas)")
    for i in nuevos:
        print(f"- {i['sentencia'] or 'sin radicado'} | {i['titulo']}")


def aprobar_item(estado, id_):
    i = estado[id_]
    archivos = descargar_providencias(i)
    i["estado"] = "aprobada"
    i["archivos"] = " | ".join(archivos)
    nuevo_archivo = not CSV_OUT.exists()
    with CSV_OUT.open("a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["aprobada", "fecha", "sentencia", "titulo",
                                          "resumen", "url", "archivos"])
        if nuevo_archivo:
            w.writeheader()
        w.writerow({"aprobada": datetime.now().isoformat(timespec="seconds"),
                    **{k: i[k] for k in ("fecha", "sentencia", "titulo", "resumen", "url", "archivos")}})


def pendientes(estado):
    return [k for k, v in estado.items() if v["estado"] == "pendiente"]


def main():
    args = set(sys.argv[1:])
    if "--lista" in args:
        estado = cargar()
        for k in pendientes(estado):
            v = estado[k]
            print(f"- {v['sentencia'] or 'sin radicado'} | {v['titulo']}\n  {v['url']}")
        return
    if "--aprobar" in args or "--aprobar-todo" in args:
        estado = cargar()
        cola = pendientes(estado)
        print(f"{len(cola)} pendiente(s)")
        for k in cola:
            v = estado[k]
            print(f"\n{v['sentencia'] or 'sin radicado'} | {v['titulo']}\n{v['resumen']}\n{v['url']}")
            r = "s" if "--aprobar-todo" in args else input("¿Aprobar y descargar? [s/n/Enter=después]: ").strip().lower()
            if r == "s":
                aprobar_item(estado, k)
            elif r == "n":
                v["estado"] = "rechazada"
            guardar(estado)
        return
    buscar_novedades()


if __name__ == "__main__":
    main()

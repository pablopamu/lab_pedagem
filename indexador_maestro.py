"""
indexador_maestro.py
--------------------
Indexador masivo para LAB21. Extrae OAs desde PDFs curriculares, 
los anota con la Taxonomía de Bloom (qwen2.5:3b) y los inyecta en SQLite.

Uso:
  python indexador_maestro.py --input priorizacion-musica.pdf
  python indexador_maestro.py --input ./mis_pdfs/
"""

import os, json, re, hashlib, argparse, unicodedata, sys, time
import sqlite3
import requests
from pathlib import Path
import fitz  # PyMuPDF
from tqdm import tqdm

# Importar tablas deterministas (curriculo.py)
from curriculo import *

# ── Configuración ─────────────────────────────────────────────────────────────
DB_FILE = "curriculo.db"
OLLAMA_URL = "http://192.168.0.6:11434"
BLOOM_MODEL = "qwen2.5:3b"
REQUEST_TIMEOUT = 60

# ── Base de Datos (con modo WAL) ──────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_FILE)
    # Habilitar Write-Ahead Logging para permitir lecturas y escrituras simultáneas
    conn.execute("PRAGMA journal_mode=WAL;")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS curriculo (
            id TEXT PRIMARY KEY,
            fuente TEXT,
            version_curricular TEXT,
            asignatura TEXT,
            asignatura_nombre TEXT,
            nivel_legible TEXT,
            nivel_codigo TEXT,
            ciclo TEXT,
            ciclo_nombre TEXT,
            tipo TEXT,
            oa TEXT,
            eje_curricular TEXT,
            bloom_verbo TEXT,
            bloom_proceso TEXT,
            bloom_conocimiento TEXT,
            bloom_celda TEXT,
            bloom_orden TEXT,
            texto_oa TEXT,
            texto_embedding TEXT
        )
    """)
    conn.commit()
    return conn

# ── Helpers ───────────────────────────────────────────────────────────────────
def nfc(texto: str) -> str: return unicodedata.normalize("NFC", texto)
def flat(texto: str) -> str: return re.sub(r"\s+", " ", nfc(texto))
def hash_contenido(texto: str) -> str: return hashlib.md5(re.sub(r"\s+", " ", texto.strip().lower()).encode("utf-8")).hexdigest()[:12]
def hash_id(asignatura: str, nivel: str, oa: str) -> str: return hashlib.md5(f"{asignatura}|{nivel}|{oa}".lower().encode("utf-8")).hexdigest()[:12]

# ── Lógica Bloom (basada 100% en curriculo.py) ────────────────────────────────
def extraer_verbos_bloom(texto: str) -> tuple[str, str, str]:
    """Atrapa TODOS los verbos, define proceso dominante y orden."""
    verbos_encontrados = []
    niveles_encontrados = []
    palabras = re.findall(r"[a-záéíóúüñ]+", texto.lower())
    
    for p in palabras:
        if p in BLOOM_VERBOS and p not in verbos_encontrados:
            verbos_encontrados.append(p)
            niveles_encontrados.append(BLOOM_VERBOS[p])
            
    if not verbos_encontrados:
        return "Comprender", "inferior", ""

    # Determinar el nivel más alto encontrado para el proceso dominante
    nivel_max = max(niveles_encontrados)
    proceso = BLOOM_NOMBRES[nivel_max]
    
    if nivel_max in [1, 2]: orden = "inferior"
    elif nivel_max == 3: orden = "medio"
    else: orden = "superior"
    
    return proceso, orden, ", ".join(verbos_encontrados)

def clasificar_conocimiento_ollama(texto: str) -> str:
    """Consulta a qwen2.5:3b para el tipo de conocimiento."""
    prompt = f"Clasifica el tipo de conocimiento predominante en este fragmento curricular:\n\n{texto[:1200]}"
    sys_prompt = "Eres un experto en la Taxonomía de Bloom. Tipos posibles: Factual, Conceptual, Procedimental, Metacognitivo. Responde ÚNICAMENTE con una de estas cuatro palabras exactas. Sin explicaciones extras."
    payload = {"model": BLOOM_MODEL, "messages": [{"role": "system", "content": sys_prompt}, {"role": "user", "content": prompt}], "stream": False, "options": {"temperature": 0.0, "num_predict": 10}}
    
    for _ in range(3):
        try:
            resp = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                respuesta = resp.json()["message"]["content"].strip()
                for cat in ["Factual", "Conceptual", "Procedimental", "Metacognitivo"]:
                    if cat.lower() in respuesta.lower(): return cat
            return "Conceptual"
        except Exception: time.sleep(2)
    return "Conceptual"

# ── Extracción y Limpieza PDF ─────────────────────────────────────────────────
def detectar_asignatura(texto_completo: str) -> tuple[str, str]:
    patron = re.search(r"([A-ZÁÉÍÓÚÜÑa-záéíóúüñ ,]+)\s*\|\s*\d+|\d+\s*\|\s*([A-ZÁÉÍÓÚÜÑa-záéíóúüñ ,]+)", texto_completo)
    if not patron: return "desconocida", "desconocida"
    nombre = (patron.group(1) or patron.group(2)).strip()
    return nombre, SLUGS_ASIGNATURA.get(nombre.lower(), re.sub(r"\s+", "_", nombre.lower()))

def limpiar_boilerplate(texto: str) -> str:
    texto = re.sub(r"[\u00ad\-]\n([a-záéíóúüñ])", r"\1", texto)
    texto = re.sub(r"[A-ZÁÉÍÓÚÜÑa-záéíóúüñ ,]+\s*\|\s*\d+", "", texto)
    texto = re.sub(r"\d+\s*\|\s*[A-ZÁÉÍÓÚÜÑa-záéíóúüñ ,]+", "", texto)
    texto = re.sub(r"A continuación.*?Bases Curriculares\.", "", texto, flags=re.DOTALL)
    texto = re.sub(r"APRENDIZAJES BASALES|APRENDIZAJES TRANSVERSALES\s*\d*", "", texto)
    texto = re.sub(r"^\s*\d{1,2}\s*$", "", texto, flags=re.MULTILINE)
    texto = re.sub(r"Los Aprendizajes Transversales aluden.*?\.", "", texto, flags=re.DOTALL)
    return texto.strip()

# ── Procesamiento por Documento ───────────────────────────────────────────────
def procesar_pdf(pdf_path: Path, conn: sqlite3.Connection):
    print(f"\n📄 Leyendo: {pdf_path.name}")
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        print(f"❌ Error al abrir {pdf_path.name}: {e}")
        return

    paginas = [{"pagina": i + 1, "texto": p.get_text("text")} for i, p in enumerate(doc) if len(p.get_text("text").strip()) > 50]
    texto_completo = "\n".join(p["texto"] for p in paginas)
    
    nombre_asignatura, slug = detectar_asignatura(texto_completo)
    match_fecha = re.search(r"(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)\s+(\d{4})", texto_completo, re.IGNORECASE)
    version = f"{match_fecha.group(1).lower()} {match_fecha.group(2)}" if match_fecha else "sin_fecha"

    chunks_crudos = []
    nivel_legible, nivel_codigo, tipo_actual = None, None, "Introduccion"

    # Fase 1: Extracción pura
    for p in paginas:
        texto = limpiar_boilerplate(p["texto"])
        tf = flat(p["texto"])
        tiene_oa = bool(re.search(r"^OA\s*\d+", p["texto"], re.MULTILINE))
        
        if tiene_oa:
            rangos_en_pagina = [nfc(r) for r, _, _ in RANGOS_SIMPLE if nfc(r) in tf]
            for clave, legible, codigo in NIVELES_SIMPLE:
                if nfc(clave) in tf and not any(nfc(clave) in rango for rango in rangos_en_pagina):
                    nivel_legible, nivel_codigo, tipo_actual = legible, codigo, "Basal"
        else:
            for clave, legible, codigo in RANGOS_SIMPLE:
                if nfc(clave) in tf: nivel_legible, nivel_codigo, tipo_actual = legible, codigo, "Transversal"

        match_sub = re.search(r"EDUCACIÓN FÍSICA Y SALUD\s*([12])", p["texto"])
        if match_sub and nivel_legible and f" — EFS {match_sub.group(1)}" not in nivel_legible:
            nivel_legible += f" — EFS {match_sub.group(1)}"

        bloques = re.split(r"(?:^|\n|\s)(OA\s*\d+)[\n\s]", texto, flags=re.MULTILINE)
        i = 0
        while i < len(bloques):
            bloque = bloques[i].strip()
            if re.match(r"^OA\s*\d+$", bloque):
                oa = re.sub(r"OA\s*(\d+)", r"OA \1", bloque)
                cont = bloques[i + 1].strip() if i + 1 < len(bloques) else ""
                if len(cont) >= 30: 
                    chunks_crudos.append({"oa": oa, "texto": re.sub(r"\s+", " ", cont).strip(), "nivel_legible": nivel_legible, "nivel_codigo": nivel_codigo, "tipo": tipo_actual})
                i += 2
            else:
                if not tiene_oa and len(bloque) >= 30: 
                    chunks_crudos.append({"oa": None, "texto": bloque, "nivel_legible": nivel_legible, "nivel_codigo": nivel_codigo, "tipo": tipo_actual})
                i += 1

    # Fase 2: Anotación Bloom e Inserción con CHECKPOINT
    c = conn.cursor()
    hashes_vistos = set()
    
    print(f"  🔍 {len(chunks_crudos)} fragmentos detectados. Iniciando Bloom y SQLite...")
    
    for ck in tqdm(chunks_crudos, desc=slug):
        if ck["tipo"] == "Transversal":
            h = hash_contenido(ck["texto"])
            if h in hashes_vistos: continue
            hashes_vistos.add(h)

        codigo_str = ",".join(ck["nivel_codigo"]) if isinstance(ck["nivel_codigo"], list) else (ck["nivel_codigo"] or "")
        nv_base = ck["nivel_codigo"][0] if isinstance(ck["nivel_codigo"], list) else (ck["nivel_codigo"] or "")
        ciclo = CICLO_POR_NIVEL.get(nv_base, "sin_ciclo")
        ciclo_nom = CICLO_NOMBRE.get(ciclo, ciclo)
        
        eje = EJES_POR_ASIGNATURA.get(slug, {}).get(ck["oa"], "Sin eje") if ck["oa"] else "Sin eje"
        if slug == "musica" and eje == "Reflexionar y contextualizar" and ciclo == "7B-2M": eje = "Reflexionar y relacionar"

        chunk_id = hash_id(slug, codigo_str, ck["oa"]) if ck["oa"] else hash_contenido(f"{ciclo}|{ck['texto']}")

        # ── CHECKPOINT REAL ──
        c.execute("SELECT id FROM curriculo WHERE id = ?", (chunk_id,))
        if c.fetchone():
            continue # Si ya existe, nos saltamos a Ollama y ahorramos tiempo
        
        # Anotación Bloom (solo corre si el chunk es nuevo)
        proceso, orden, verbos_csv = extraer_verbos_bloom(ck["texto"]) if ck["oa"] else ("", "", "")
        conocimiento = clasificar_conocimiento_ollama(ck["texto"]) if ck["oa"] else ""
        celda = f"{proceso}-{conocimiento}" if ck["oa"] else ""

        # Enriquecimiento del texto embedding
        if ck["oa"]:
            partes_emb = [
                f"Asignatura: {nombre_asignatura}.",
                f"Nivel: {ck['nivel_legible']}.",
                f"Ciclo: {ciclo_nom}.",
                f"Eje curricular: {eje}.",
                f"Nivel cognitivo (Bloom): {proceso}.",
                f"Objetivo de Aprendizaje {ck['oa']} (tipo Basal): {ck['texto']}"
            ]
        else:
            partes_emb = [f"Aprendizaje {'Transversal' if ck['tipo'] == 'Transversal' else 'contextual'}.", f"Ciclo: {ciclo_nom}.", ck["texto"]]
        texto_embedding = " ".join(partes_emb)

        # Inserción
        c.execute("""
            INSERT OR IGNORE INTO curriculo 
            (id, fuente, version_curricular, asignatura, asignatura_nombre, nivel_legible, nivel_codigo, ciclo, ciclo_nombre, tipo, oa, eje_curricular, bloom_verbo, bloom_proceso, bloom_conocimiento, bloom_celda, bloom_orden, texto_oa, texto_embedding)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            chunk_id, pdf_path.name, version, slug, nombre_asignatura, ck["nivel_legible"] or "", codigo_str, ciclo, ciclo_nom, ck["tipo"], ck["oa"] or "", eje, verbos_csv, proceso, conocimiento, celda, orden, ck["texto"], texto_embedding
        ))
        conn.commit()

# ── Ejecución CLI (Soporta Batch) ─────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Indexador masivo para LAB21 (SQLite + Bloom)")
    parser.add_argument("--input", required=True, help="Ruta a un PDF o a una carpeta con varios PDFs")
    args = parser.parse_args()

    conn = init_db()
    ruta = Path(args.input)

    # Validar conexión a Ollama antes de empezar
    try:
        requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        print(f"✓ Ollama detectado en {OLLAMA_URL}")
    except Exception:
        print(f"❌ Error: No se puede conectar a Ollama en {OLLAMA_URL}. Levanta el servidor primero.")
        sys.exit(1)

    # Recolectar PDFs
    pdfs = []
    if ruta.is_dir():
        pdfs = list(ruta.glob("*.pdf"))
    elif ruta.is_file() and ruta.suffix.lower() == ".pdf":
        pdfs = [ruta]

    if not pdfs:
        print(f"⚠️ No se encontraron archivos PDF en {ruta}")
        sys.exit(1)

    print(f"🚀 Iniciando indexación en lote ({len(pdfs)} documentos)")
    for pdf in pdfs:
        procesar_pdf(pdf, conn)

    print("\n✅ Indexación completada. Base de datos actualizada.")
    conn.close()

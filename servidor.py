import os, json, time, asyncio, sqlite3, httpx, re
from contextlib import contextmanager
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
from typing import List, Dict, Any, Optional

# Carga de variables de entorno (API de Google)
from dotenv import load_dotenv
load_dotenv() 

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# Usamos el endpoint de Gemini compatible con el estándar de OpenAI
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CURRICULO_DB = os.path.join(BASE_DIR, "curriculo.db")

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# ── Config ────────────────────────────────────────────────────────────────────
app           = FastAPI()
DIR_PROMPTS   = "prompts"
DIR_PDFS      = "pdfs"          
DB_FILE       = "experimentos.db"

os.makedirs(DIR_PROMPTS, exist_ok=True)
os.makedirs(DIR_PDFS,    exist_ok=True)

if not os.listdir(DIR_PROMPTS):
    with open(os.path.join(DIR_PROMPTS, "experto_pedagogico.txt"), "w", encoding="utf-8") as f:
        f.write("Eres un asistente pedagógico experto en el currículo chileno. Eres directo y riguroso.")

_abort_events: Dict[str, asyncio.Event] = {}

# ── DB helpers ────────────────────────────────────────────────────────────────
@contextmanager
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

@contextmanager
def get_curriculo_db():
    conn = sqlite3.connect(CURRICULO_DB)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS sesiones (
            id_sesion            TEXT PRIMARY KEY,
            titulo_ia            TEXT,
            titulo_humano        TEXT,
            subtitulo_humano     TEXT,
            reacciones           TEXT,
            fecha                TEXT,
            duracion_total_s     REAL    DEFAULT 0,
            es_resesion          INTEGER DEFAULT 0,
            sesion_origen        TEXT    DEFAULT NULL,
            system_prompt        TEXT    DEFAULT '',
            parametros_iniciales TEXT    DEFAULT '{}',
            parametros_finales   TEXT    DEFAULT '{}',
            modelo               TEXT    DEFAULT ''
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS interacciones (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            id_sesion            TEXT,
            turno                INTEGER,
            modelo               TEXT,
            prompt_usuario       TEXT,
            respuesta_modelo     TEXT,
            tiempo_escritura_s   REAL    DEFAULT 0,
            tiempo_inferencia_s  REAL    DEFAULT 0,
            tps                  REAL    DEFAULT 0,
            prompt_tokens        INTEGER DEFAULT 0,
            output_tokens        INTEGER DEFAULT 0,
            delta_tokens         INTEGER DEFAULT 0,
            ctx_acumulado        INTEGER DEFAULT 0,
            ratio_proc_gen       REAL    DEFAULT 0,
            editado              INTEGER DEFAULT 0,
            abortado             INTEGER DEFAULT 0,
            FOREIGN KEY(id_sesion) REFERENCES sesiones(id_sesion) ON DELETE CASCADE
        )""")

init_db()

# ── Pydantic ──────────────────────────────────────────────────────────────────
class IniciarSesionPayload(BaseModel):
    id_sesion:     str
    modelo:        str
    system_prompt: str
    parametros:    Dict[str, Any]
    es_resesion:   bool         = False
    sesion_origen: Optional[str] = None

class InferenciaPayload(BaseModel):
    id_sesion:           str
    modelo:              str
    system_prompt:       str
    origen_prompt:       str
    mensajes_historial:  List[Dict[str, str]]
    prompt_actual:       str
    temperatura:         float
    top_p:               float
    top_k:               int
    max_tokens:          int
    timeout_s:           int   = 180
    tiempo_escritura_s:  float = 0.0
    prompt_tokens_prev:  int   = 0
    rag_activo:          bool          = False
    rag_asignaturas:     List[str]     = []
    rag_nivel_forzado:   Optional[str] = None
    oas_ids:             List[str]     = []

class GuardarTurnoPayload(BaseModel):
    id_sesion:           str
    turno:               int
    modelo:              str
    prompt_usuario:      str
    respuesta_modelo:    str
    tiempo_escritura_s:  float
    tiempo_inferencia_s: float
    tps:                 float
    prompt_tokens:       int
    output_tokens:       int
    delta_tokens:        int   = 0
    ctx_acumulado:       int   = 0
    ratio_proc_gen:      float = 0.0
    abortado:            bool  = False

class EditarTurnoPayload(BaseModel):
    id_sesion: str; turno: int; campo: str; contenido: str

class RenombrarSesionPayload(BaseModel):
    id_sesion: str; titulo_humano: str

class EliminarTurnosPayload(BaseModel):
    id_sesion: str; turnos: List[int]

class FinalizarPayload(BaseModel):
    id_sesion: str; subtitulo_humano: str; reacciones_viscerales: str

class NuevoPromptPayload(BaseModel):
    nombre: str; contenido: str

# ── Utils ─────────────────────────────────────────────────────────────────────
def safe_prompt_path(nombre: str) -> str:
    limpio = os.path.basename(nombre)
    ruta   = os.path.realpath(os.path.join(DIR_PROMPTS, limpio))
    base   = os.path.realpath(DIR_PROMPTS)
    if not ruta.startswith(base + os.sep):
        raise HTTPException(status_code=400, detail="Ruta inválida")
    return ruta

def safe_pdf_path(nombre: str) -> str:
    limpio = os.path.basename(nombre)
    ruta   = os.path.realpath(os.path.join(DIR_PDFS, limpio))
    base   = os.path.realpath(DIR_PDFS)
    if not ruta.startswith(base + os.sep):
        raise HTTPException(status_code=400, detail="Ruta inválida")
    return ruta

# ── Rutas estáticas ───────────────────────────────────────────────────────────
@app.get("/")
async def index():
    return FileResponse("index.html")

# ── Telemetría ────────────────────────────────────────────────────────────────
@app.get("/api/telemetria")
async def get_telemetria():
    if HAS_PSUTIL:
        return {"cpu": psutil.cpu_percent(), "ram": psutil.virtual_memory().percent}
    return {"cpu": 0, "ram": 0, "mock": True}

# ── Modelos (Mock para Gemini) ────────────────────────────────────────────────
@app.get("/api/modelos")
async def get_modelos():
    # Engañamos al frontend para que liste Gemini por defecto
    meta = {"cuantizacion": "Cloud", "tamano": "API", "familia": "Gemini", "contexto_max": 1048576, "size_gb": 0}
    return {"data": [{"id": "gemini-2.5-flash-lite", "metadatos": meta}]}

@app.get("/api/ollama/estado")
async def ollama_estado():
    # Mantenemos el endpoint vivo para que la UI no arroje error
    return {"ocupado": True, "modelos": [{"modelo": "gemini-2.5-flash-lite"}]}

# ── Prompts ───────────────────────────────────────────────────────────────────
@app.get("/api/prompts")
async def listar_prompts():
    return {"prompts": [f for f in sorted(os.listdir(DIR_PROMPTS)) if f.endswith(".txt")]}

@app.get("/api/prompts/{nombre}")
async def cargar_prompt(nombre: str):
    with open(safe_prompt_path(nombre), "r", encoding="utf-8") as f:
        return {"contenido": f.read()}

@app.post("/api/prompts")
async def crear_prompt(payload: NuevoPromptPayload):
    if not payload.nombre.endswith(".txt"):
        payload.nombre += ".txt"
    with open(safe_prompt_path(payload.nombre), "w", encoding="utf-8") as f:
        f.write(payload.contenido)
    return {"status": "ok"}

# ── PDFs (biblioteca de referencia, solo lectura) ─────────────────────────────
@app.get("/api/pdfs")
async def listar_pdfs():
    archivos = []
    for f in sorted(os.listdir(DIR_PDFS)):
        if f.lower().endswith(".pdf"):
            ruta = os.path.join(DIR_PDFS, f)
            archivos.append({
                "nombre": f,
                "size_kb": round(os.path.getsize(ruta) / 1024, 1)
            })
    return {"pdfs": archivos}

@app.get("/api/pdfs/{nombre_archivo}")
async def servir_pdf(nombre_archivo: str):
    ruta = safe_pdf_path(nombre_archivo)
    if not os.path.exists(ruta):
        raise HTTPException(status_code=404, detail=f"PDF '{nombre_archivo}' no encontrado")
    return FileResponse(
        path=ruta,
        media_type="application/pdf",
        filename=nombre_archivo,
        headers={"Content-Disposition": f"inline; filename=\"{nombre_archivo}\""}
    )

# ── Explorador Curricular (SQLite) ────────────────────────────────────────────
@app.get("/api/curriculo/filtros")
async def get_filtros_curriculo():
    with get_curriculo_db() as conn:
        c = conn.cursor()
        c.execute("""SELECT DISTINCT asignatura, asignatura_nombre
                     FROM curriculo WHERE tipo != 'Transversal'
                     ORDER BY asignatura_nombre""")
        asignaturas = [{"slug": r[0], "nombre": r[1]} for r in c.fetchall()]

        c.execute("""SELECT DISTINCT nivel_codigo, nivel_legible
                     FROM curriculo WHERE nivel_legible != ''
                     ORDER BY nivel_codigo""")
        niveles = [{"codigo": r[0], "nombre": r[1]} for r in c.fetchall()]

        c.execute("""SELECT DISTINCT eje_curricular
                     FROM curriculo WHERE eje_curricular IS NOT NULL AND eje_curricular != ''
                     ORDER BY eje_curricular""")
        ejes = [r[0] for r in c.fetchall()]

    return {"asignaturas": asignaturas, "niveles": niveles, "ejes": ejes}

@app.get("/api/curriculo/oas")
async def get_oas(asignatura: str = "", nivel: str = "", eje: str = "", bloom: str = ""):
    with get_curriculo_db() as conn:
        c = conn.cursor()
        query  = """SELECT id, oa, eje_curricular, bloom_proceso, texto_oa, nivel_legible
                    FROM curriculo WHERE tipo='Basal'"""
        params = []

        if asignatura:
            query += " AND asignatura=?"
            params.append(asignatura)
        if nivel:
            query += " AND nivel_codigo LIKE ?"
            params.append(f"%{nivel}%")
        if eje:
            query += " AND eje_curricular=?"
            params.append(eje)
        if bloom and bloom != "todos":
            query += " AND bloom_proceso=?"
            params.append(int(bloom))

        query += " ORDER BY CAST(SUBSTR(oa, 4) AS INTEGER)"
        c.execute(query, params)
        rows = c.fetchall()

    oas = [{"id": r[0], "oa": r[1], "eje": r[2], "bloom": r[3], "texto": r[4], "nivel": r[5]}
           for r in rows]
    return {"oas": oas}

# ── Sesiones CRUD ─────────────────────────────────────────────────────────────
@app.post("/api/sesiones/iniciar")
async def iniciar_sesion(payload: IniciarSesionPayload):
    with get_db() as conn:
        conn.execute("""INSERT OR IGNORE INTO sesiones
                        (id_sesion, titulo_ia, subtitulo_humano, reacciones, fecha,
                         duracion_total_s, es_resesion, sesion_origen, system_prompt,
                         parametros_iniciales, modelo)
                        VALUES (?,?,'','',?,0,?,?,?,?,?)""",
                     (payload.id_sesion, f"Sesión {payload.modelo}",
                      datetime.now().isoformat(), int(payload.es_resesion),
                      payload.sesion_origen, payload.system_prompt,
                      json.dumps(payload.parametros), payload.modelo))
    return {"status": "ok"}

@app.post("/api/sesiones/turno")
async def guardar_turno(payload: GuardarTurnoPayload):
    with get_db() as conn:
        conn.execute("""INSERT INTO interacciones
                        (id_sesion, turno, modelo, prompt_usuario, respuesta_modelo,
                         tiempo_escritura_s, tiempo_inferencia_s, tps, prompt_tokens,
                         output_tokens, delta_tokens, ctx_acumulado, ratio_proc_gen, abortado)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (payload.id_sesion, payload.turno, payload.modelo,
                      payload.prompt_usuario, payload.respuesta_modelo,
                      payload.tiempo_escritura_s, payload.tiempo_inferencia_s,
                      payload.tps, payload.prompt_tokens, payload.output_tokens,
                      payload.delta_tokens, payload.ctx_acumulado,
                      payload.ratio_proc_gen, int(payload.abortado)))
        conn.execute("""UPDATE sesiones
                        SET duracion_total_s=(
                            SELECT COALESCE(SUM(tiempo_escritura_s+tiempo_inferencia_s),0)
                            FROM interacciones WHERE id_sesion=?
                        ) WHERE id_sesion=?""",
                     (payload.id_sesion, payload.id_sesion))
    return {"status": "ok"}

@app.put("/api/sesiones/turno/editar")
async def editar_turno(payload: EditarTurnoPayload):
    if payload.campo not in ("prompt_usuario", "respuesta_modelo"):
        raise HTTPException(status_code=400, detail="Campo inválido")
    with get_db() as conn:
        conn.execute(f"UPDATE interacciones SET {payload.campo}=?, editado=1 "
                     f"WHERE id_sesion=? AND turno=?",
                     (payload.contenido, payload.id_sesion, payload.turno))
    return {"status": "ok"}

@app.put("/api/sesiones/renombrar")
async def renombrar_sesion(payload: RenombrarSesionPayload):
    with get_db() as conn:
        conn.execute("UPDATE sesiones SET titulo_humano=? WHERE id_sesion=?",
                     (payload.titulo_humano, payload.id_sesion))
    return {"status": "ok"}

@app.delete("/api/sesiones/{id_sesion}")
async def eliminar_sesion(id_sesion: str):
    with get_db() as conn:
        conn.execute("DELETE FROM interacciones WHERE id_sesion=?", (id_sesion,))
        conn.execute("DELETE FROM sesiones WHERE id_sesion=?", (id_sesion,))
    return {"status": "ok"}

@app.delete("/api/sesiones/{id_sesion}/turnos")
async def eliminar_turnos(id_sesion: str, payload: EliminarTurnosPayload):
    with get_db() as conn:
        for t in payload.turnos:
            conn.execute("DELETE FROM interacciones WHERE id_sesion=? AND turno=?",
                         (id_sesion, t))
    return {"status": "ok"}

# ── Cargar & Leer ─────────────────────────────────────────────────────────────
@app.get("/api/sesiones")
async def listar_sesiones():
    with get_db() as conn:
        rows = conn.execute("""SELECT id_sesion, titulo_ia, titulo_humano, subtitulo_humano,
                                      fecha, duracion_total_s, es_resesion, sesion_origen
                               FROM sesiones ORDER BY fecha DESC LIMIT 100""").fetchall()
    return {"sesiones": [{
        "id_sesion":       r["id_sesion"],
        "titulo":          r["titulo_humano"] or r["titulo_ia"] or r["id_sesion"],
        "titulo_ia":       r["titulo_ia"] or r["id_sesion"],
        "titulo_humano":   r["titulo_humano"],
        "subtitulo":       r["subtitulo_humano"] or "",
        "fecha":           r["fecha"],
        "duracion_total_s": r["duracion_total_s"] or 0,
        "es_resesion":     bool(r["es_resesion"]),
        "sesion_origen":   r["sesion_origen"]
    } for r in rows]}

@app.get("/api/sesiones/{id_sesion}")
async def cargar_sesion(id_sesion: str):
    with get_db() as conn:
        s = conn.execute("SELECT * FROM sesiones WHERE id_sesion=?",
                         (id_sesion,)).fetchone()
        if not s:
            raise HTTPException(status_code=404)
        i_rows = conn.execute("""SELECT * FROM interacciones
                                 WHERE id_sesion=? ORDER BY turno""",
                              (id_sesion,)).fetchall()

    interacciones = [{
        "turno":         r["turno"],
        "role_user":     r["prompt_usuario"],
        "role_assistant": r["respuesta_modelo"],
        "abortado":      bool(r["abortado"]),
        "editado":       bool(r["editado"]),
        "metricas": {
            "tiempo_escritura_s":  r["tiempo_escritura_s"],
            "tiempo_inferencia_s": r["tiempo_inferencia_s"],
            "tps":                 r["tps"],
            "prompt_tokens":       r["prompt_tokens"],
            "output_tokens":       r["output_tokens"],
            "delta_tokens":        r["delta_tokens"],
            "ctx_acumulado":       r["ctx_acumulado"],
            "ratio_proc_gen":      r["ratio_proc_gen"]
        }
    } for r in i_rows]

    return {
        "id_sesion":          s["id_sesion"],
        "titulo_ia":          s["titulo_ia"],
        "titulo_humano":      s["titulo_humano"],
        "subtitulo_humano":   s["subtitulo_humano"],
        "reacciones_viscerales": s["reacciones"],
        "fecha":              s["fecha"],
        "duracion_total_s":   s["duracion_total_s"],
        "es_resesion":        bool(s["es_resesion"]),
        "sesion_origen":      s["sesion_origen"],
        "system_prompt":      s["system_prompt"],
        "parametros_iniciales": json.loads(s["parametros_iniciales"] or "{}"),
        "modelo_activo":      {"id": s["modelo"]},
        "interacciones":      interacciones
    }

@app.get("/api/sesiones/{id_sesion}/guion")
async def exportar_guion(id_sesion: str):
    with get_db() as conn:
        s = conn.execute("""SELECT titulo_humano, titulo_ia, modelo, system_prompt,
                                   parametros_iniciales, fecha
                            FROM sesiones WHERE id_sesion=?""",
                         (id_sesion,)).fetchone()
        if not s:
            raise HTTPException(status_code=404)
        i_rows = conn.execute("""SELECT turno, prompt_usuario FROM interacciones
                                 WHERE id_sesion=? AND prompt_usuario IS NOT NULL
                                 ORDER BY turno""",
                              (id_sesion,)).fetchall()

    titulo = s["titulo_humano"] or s["titulo_ia"] or id_sesion
    modelo = s["modelo"]
    sys_p  = s["system_prompt"]
    fecha  = s["fecha"]
    prompts = [{"turno": r["turno"], "prompt": r["prompt_usuario"]} for r in i_rows]

    txt = f"# GUIÓN — {titulo}\n# Fecha: {fecha}\n# Modelo: {modelo} (Gemini)\n\n"
    for p in prompts:
        txt += f"[{p['turno']+1}] {p['prompt']}\n\n"

    # Exportamos el script ajustado a la API de Gemini
    script  = f'"""Guion de replicacion — {titulo}\\nGenerado por LAB21 v4.5"""\n\nimport httpx, json, os\n\n'
    script += f'GEMINI_URL = "{GEMINI_URL}"\nMODELO = "{modelo}"\nSYSTEM_PROMPT = """{sys_p}"""\n\n'
    script += 'PROMPTS = [\n'
    for p in prompts:
        script += f'    "{p["prompt"].replace(chr(92),chr(92)*2).replace(chr(34),chr(92)+chr(34))}",\n'
    script += ']\n\n'
    script += '''def run():
    api_key = os.getenv("GEMINI_API_KEY", "TU_LLAVE_AQUI")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    historial = []
    
    for i, prompt in enumerate(PROMPTS):
        print(f"\\n[{i+1}/{len(PROMPTS)}] Enviando a Gemini...")
        mensajes = [{"role": "system", "content": SYSTEM_PROMPT}] + historial + [{"role": "user", "content": prompt}]
        payload = {"model": MODELO, "messages": mensajes, "stream": False}
        
        resp = httpx.post(GEMINI_URL, json=payload, headers=headers, timeout=300)
        
        if resp.status_code == 200:
            data = resp.json()
            contenido = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            print(f"Respuesta:\\n{contenido}\\n")
            historial.extend([{"role": "user", "content": prompt}, {"role": "assistant", "content": contenido}])
        else:
            print(f"Error HTTP {resp.status_code}: {resp.text}")

if __name__ == "__main__": run()'''

    return {"titulo": titulo, "prompts": prompts, "texto_plano": txt, "script_python": script}

# ── Finalizar (Autotítulo adaptado a Gemini) ──────────────────────────────────
@app.post("/api/sesiones/finalizar")
async def finalizar_sesion(payload: FinalizarPayload):
    with get_db() as conn:
        row = conn.execute("SELECT modelo, titulo_ia FROM sesiones WHERE id_sesion=?",
                           (payload.id_sesion,)).fetchone()
        if not row:
            return {"status": "error"}
        titulo_ia = row["titulo_ia"]

        if titulo_ia.startswith("Sesión ") or not titulo_ia:
            msgs_rows = conn.execute("""SELECT prompt_usuario, respuesta_modelo
                                        FROM interacciones WHERE id_sesion=? LIMIT 3""",
                                     (payload.id_sesion,)).fetchall()
            
            msgs = [{"role": "system", "content": "Titulador tecnico. Responde SOLO el titulo, max 5 palabras, sin puntuacion final."}]
            for r in msgs_rows:
                msgs.append({"role": "user",      "content": str(r["prompt_usuario"])[:400]})
                msgs.append({"role": "assistant", "content": str(r["respuesta_modelo"])[:400]})
            msgs.append({"role": "user", "content": "Titulo tecnico de esta conversacion."})
            
            headers = {
                "Authorization": f"Bearer {GEMINI_API_KEY}",
                "Content-Type": "application/json"
            }
            
            try:
                async with httpx.AsyncClient(timeout=10, headers=headers) as client:
                    r = await client.post(GEMINI_URL, json={
                        "model": "gemini-2.5-flash-lite", 
                        "messages": msgs, 
                        "stream": False,
                        "temperature": 0.3,
                        "max_tokens": 20
                    })
                    if r.status_code == 200:
                        data = r.json()
                        titulo_nuevo = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip().strip('"\'')
                        if titulo_nuevo:
                            titulo_ia = titulo_nuevo
            except Exception as e:
                print(f"Error generando título con Gemini: {e}")

        conn.execute("""UPDATE sesiones SET titulo_ia=?, subtitulo_humano=?, reacciones=?
                        WHERE id_sesion=?""",
                     (titulo_ia, payload.subtitulo_humano,
                      payload.reacciones_viscerales, payload.id_sesion))

    return {"status": "ok", "titulo_ia": titulo_ia}

# ── Inferencia Principal (Solo Gemini) ────────────────────────────────────────
@app.post("/api/inferencia/abortar")
async def abortar(payload: BaseModel):
    id_sesion = getattr(payload, "id_sesion", None)
    if id_sesion and id_sesion in _abort_events:
        _abort_events[id_sesion].set()
    return {"status": "ok"}

@app.post("/api/inferencia")
async def inferencia(payload: InferenciaPayload, request: Request):
    print(f"DEBUG - OAs recibidos del frontend: {payload.oas_ids}")
    
    system_prompt_final = payload.system_prompt
    oas_inyectados      = []

    # ── Inyección de OAs seleccionados desde el explorador curricular ──────────
    if payload.oas_ids:
        ids_validos = [str(oid).strip() for oid in payload.oas_ids if oid]

        if ids_validos:
            with get_curriculo_db() as conn:
                placeholders = ",".join("?" * len(ids_validos))
                rows = conn.execute(
                    f"SELECT id, oa, texto_oa, texto_embedding FROM curriculo "
                    f"WHERE id IN ({placeholders})",
                    ids_validos
                ).fetchall()

            if rows:
                bloque_oas  = "\n\n[OBJETIVOS DE APRENDIZAJE SELECCIONADOS PARA ESTA TAREA]:\n"
                for r in rows:
                    texto = r["texto_embedding"] or r["texto_oa"] or ""
                    bloque_oas += f"\n---\n{texto}"
                    oas_inyectados.append({
                        "id":    r["id"],
                        "oa":    r["oa"],
                        "texto": (r["texto_oa"] or "")[:200]
                    })
                
                print("\n=== 🎯 DEBUG: OAs INYECTADOS AL LLM ===")
                for oa in oas_inyectados:
                    print(f"[{oa['oa']}] {oa['texto'][:120]}...")
                print("========================================\n")

                bloque_oas += "\n[FIN DE OBJETIVOS]\nDiseña tu respuesta integrando estrictamente los objetivos mencionados arriba."
                system_prompt_final += bloque_oas

    # ── Armado de mensajes estándar (OpenAI/Gemini compatible) ──────────
    mensajes = [{"role": "system", "content": system_prompt_final}]
    mensajes.extend(payload.mensajes_historial)
    mensajes.append({"role": "user", "content": payload.prompt_actual})

    # ── Payload para Google AI Studio ──────────
    gemini_payload = {
        "model": "gemini-2.5-flash-lite",
        "messages": mensajes,
        "stream": True,
        "temperature": payload.temperatura
    }

    headers = {
        "Authorization": f"Bearer {GEMINI_API_KEY}",
        "Content-Type": "application/json"
    }

    abort_event = asyncio.Event()
    _abort_events[payload.id_sesion] = abort_event
    timeout = httpx.Timeout(connect=10.0, read=float(payload.timeout_s), write=10.0, pool=5.0)

    async def stream_gen():
        start, prompt_tokens, output_tokens = time.time(), 0, 0

        if oas_inyectados:
            yield f"[OAS]||{json.dumps(oas_inyectados)}\n"

        try:
            print("🟢 INFO: Enviando prompt hacia Google Gemini API...")
            async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
                async with client.stream("POST", GEMINI_URL, json=gemini_payload) as resp:
                    
                    if resp.status_code != 200:
                        error_body = await resp.aread()
                        print(f"\n❌ ERROR GEMINI ({resp.status_code}): {error_body.decode('utf-8')}\n")
                        yield f"[ERROR]||Gemini rechazó el prompt (HTTP {resp.status_code})\n"
                        return

                    async for line in resp.aiter_lines():
                        if abort_event.is_set() or await request.is_disconnected():
                            await resp.aclose()
                            yield "[ABORTED]||\n" if abort_event.is_set() else ""
                            return
                        
                        if not line.startswith("data: "):
                            continue
                            
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break
                            
                        try:
                            data = json.loads(data_str)
                        except:
                            continue

                        if "choices" in data and len(data["choices"]) > 0:
                            delta = data["choices"][0].get("delta", {})
                            if "content" in delta:
                                token = delta["content"]
                                output_tokens += 1
                                
                                elapsed = time.time() - start
                                tps_live = round(output_tokens / elapsed, 1) if elapsed > 0 else 0
                                tok_enc = token.replace("\\", "\\\\").replace("\n", "\\n")
                                
                                yield f"[TK]||{tok_enc}||{output_tokens}||{tps_live}\n"

                    # Cierre del streaming
                    total = round(time.time() - start, 2)
                    tps_final = round(output_tokens / total, 2) if total > 0 else 0
                    yield f"[DONE]||{total}||{tps_final}||0||{output_tokens}||0||0\n"

        except httpx.TimeoutException:
            yield "[ERROR]||timeout (API de Google no respondió a tiempo)\n"
        except Exception as e:
            yield f"[ERROR]||{str(e)[:120]}\n"
        finally:
            _abort_events.pop(payload.id_sesion, None)

    return StreamingResponse(stream_gen(), media_type="text/plain")

# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
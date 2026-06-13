"""
curriculo.py — Datos curriculares centralizados para LAB21.
Importado por chunker_generico.py y servidor.py.
Para agregar una asignatura nueva: SLUGS_ASIGNATURA, EJES_POR_ASIGNATURA y NIVEL_REGEX.
"""
import re

# ── Niveles ───────────────────────────────────────────────────────────────────
NIVELES_SIMPLE = [
    ("TERCERO MEDIO Y CUARTO MEDIO", "Tercero y Cuarto Medio", ["3M", "4M"]),
    ("PRIMERO BÁSICO",  "Primero Básico",  "1B"),
    ("SEGUNDO BÁSICO",  "Segundo Básico",  "2B"),
    ("TERCERO BÁSICO",  "Tercero Básico",  "3B"),
    ("CUARTO BÁSICO",   "Cuarto Básico",   "4B"),
    ("QUINTO BÁSICO",   "Quinto Básico",   "5B"),
    ("SEXTO BÁSICO",    "Sexto Básico",    "6B"),
    ("SÉPTIMO BÁSICO",  "Séptimo Básico",  "7B"),
    ("OCTAVO BÁSICO",   "Octavo Básico",   "8B"),
    ("PRIMERO MEDIO",   "Primero Medio",   "1M"),
    ("SEGUNDO MEDIO",   "Segundo Medio",   "2M"),
]

RANGOS_SIMPLE = [
    ("PRIMERO BÁSICO A SEXTO BÁSICO",  "1° Básico a 6° Básico",  ["1B","2B","3B","4B","5B","6B"]),
    ("SÉPTIMO BÁSICO A SEGUNDO MEDIO", "7° Básico a 2° Medio",   ["7B","8B","1M","2M"]),
]

CICLO_POR_NIVEL = {
    "1B": "1B-6B", "2B": "1B-6B", "3B": "1B-6B",
    "4B": "1B-6B", "5B": "1B-6B", "6B": "1B-6B",
    "7B": "7B-2M", "8B": "7B-2M", "1M": "7B-2M", "2M": "7B-2M",
}

CICLO_NOMBRE = {
    "1B-6B": "1° Básico a 6° Básico",
    "7B-2M": "7° Básico a 2° Medio",
}

# ── Asignaturas ───────────────────────────────────────────────────────────────
SLUGS_ASIGNATURA = {
    "música":                                  "musica",
    "educación física y salud":                "educacion_fisica",
    "lenguaje y comunicación":                 "lenguaje",
    "matemática":                              "matematica",
    "historia, geografía y ciencias sociales": "ciencias_sociales",
    "ciencias naturales":                      "ciencias_naturales",
    "orientación":                             "orientacion",
    "tecnología":                              "tecnologia",
    "artes visuales":                          "artes_visuales",
    "inglés":                                  "ingles",
}

# Nombre legible por slug (inverso de SLUGS_ASIGNATURA)
NOMBRE_ASIGNATURA = {v: k.title() for k, v in SLUGS_ASIGNATURA.items()}

# ── Ejes curriculares por asignatura ─────────────────────────────────────────
EJES_POR_ASIGNATURA = {
    "musica": {
        "OA 1": "Escuchar y apreciar",
        "OA 2": "Escuchar y apreciar",
        "OA 3": "Interpretar y crear",
        "OA 4": "Interpretar y crear",
        "OA 5": "Interpretar y crear",
        "OA 6": "Interpretar y crear",
        "OA 7": "Reflexionar y contextualizar",
    },
    "artes_visuales": {
        "OA 1": "Apreciar y responder",
        "OA 2": "Apreciar y responder",
        "OA 3": "Crear",
        "OA 4": "Crear",
        "OA 5": "Crear",
        "OA 6": "Reflexionar y contextualizar",
    },
    "educacion_fisica": {
        "OA 1": "Habilidades motrices",
        "OA 2": "Habilidades motrices",
        "OA 3": "Vida activa y saludable",
        "OA 4": "Vida activa y saludable",
        "OA 5": "Juego limpio",
        "OA 6": "Juego limpio",
    },
    "lenguaje": {
        "OA 1":  "Lectura",  "OA 2":  "Lectura",  "OA 3":  "Lectura",
        "OA 4":  "Lectura",  "OA 5":  "Lectura",  "OA 6":  "Lectura",
        "OA 7":  "Escritura","OA 8":  "Escritura","OA 9":  "Escritura",
        "OA 10": "Escritura","OA 11": "Escritura",
        "OA 12": "Comunicación oral",
        "OA 13": "Comunicación oral",
        "OA 14": "Comunicación oral",
    },
    "matematica": {
        "OA 1":  "Números y operaciones",
        "OA 2":  "Números y operaciones",
        "OA 3":  "Números y operaciones",
        "OA 4":  "Números y operaciones",
        "OA 5":  "Números y operaciones",
        "OA 6":  "Números y operaciones",
        "OA 7":  "Álgebra y funciones",
        "OA 8":  "Álgebra y funciones",
        "OA 9":  "Álgebra y funciones",
        "OA 10": "Geometría",
        "OA 11": "Geometría",
        "OA 12": "Geometría",
        "OA 13": "Datos y probabilidades",
        "OA 14": "Datos y probabilidades",
        "OA 15": "Datos y probabilidades",
    },
    "ciencias_naturales": {
        "OA 1":  "Ciencias de la vida",
        "OA 2":  "Ciencias de la vida",
        "OA 3":  "Ciencias de la vida",
        "OA 4":  "Ciencias de la vida",
        "OA 5":  "Ciencias físicas y químicas",
        "OA 6":  "Ciencias físicas y químicas",
        "OA 7":  "Ciencias físicas y químicas",
        "OA 8":  "Ciencias de la Tierra y el Universo",
        "OA 9":  "Ciencias de la Tierra y el Universo",
        "OA 10": "Habilidades de pensamiento científico",
        "OA 11": "Habilidades de pensamiento científico",
        "OA 12": "Habilidades de pensamiento científico",
    },
    "ciencias_sociales": {
        "OA 1":  "Historia",
        "OA 2":  "Historia",
        "OA 3":  "Historia",
        "OA 4":  "Historia",
        "OA 5":  "Geografía",
        "OA 6":  "Geografía",
        "OA 7":  "Geografía",
        "OA 8":  "Formación ciudadana",
        "OA 9":  "Formación ciudadana",
        "OA 10": "Formación ciudadana",
        "OA 11": "Habilidades de pensamiento histórico",
        "OA 12": "Habilidades de pensamiento histórico",
    },
    "orientacion": {
        "OA 1": "Crecimiento y autoconocimiento",
        "OA 2": "Crecimiento y autoconocimiento",
        "OA 3": "Habilidades para la vida",
        "OA 4": "Habilidades para la vida",
        "OA 5": "Ciudadanía y participación",
        "OA 6": "Ciudadanía y participación",
    },
    "tecnologia": {
        "OA 1": "Proceso de diseño",
        "OA 2": "Proceso de diseño",
        "OA 3": "Proceso de diseño",
        "OA 4": "Cultura tecnológica",
        "OA 5": "Cultura tecnológica",
        "OA 6": "Cultura tecnológica",
    },
    "ingles": {
        "OA 1": "Comprensión auditiva",
        "OA 2": "Comprensión auditiva",
        "OA 3": "Comprensión lectora",
        "OA 4": "Comprensión lectora",
        "OA 5": "Expresión oral",
        "OA 6": "Expresión oral",
        "OA 7": "Expresión escrita",
        "OA 8": "Expresión escrita",
    },
}

# ── Bloom ─────────────────────────────────────────────────────────────────────
BLOOM_VERBOS = {
    "identificar": 1, "reconocer": 1, "nombrar": 1, "listar": 1,
    "describir": 1, "recordar": 1, "definir": 1,
    "explicar": 2, "interpretar": 2, "clasificar": 2, "comparar": 2,
    "resumir": 2, "inferir": 2, "relacionar": 2, "distinguir": 2,
    "representar": 2,
    "aplicar": 3, "usar": 3, "ejecutar": 3, "implementar": 3,
    "cantar": 3, "tocar": 3, "practicar": 3, "expresar": 3,
    "escuchar": 3, "utilizar": 3, "demostrar": 3,
    "analizar": 4, "diferenciar": 4, "organizar": 4, "examinar": 4,
    "descomponer": 4, "contrastar": 4, "investigar": 4,
    "evaluar": 5, "valorar": 5, "juzgar": 5, "criticar": 5,
    "justificar": 5, "defender": 5, "apreciar": 5, "reflexionar": 5,
    "fundamentar": 5,
    "crear": 6, "diseñar": 6, "componer": 6, "elaborar": 6,
    "construir": 6, "planificar": 6, "producir": 6, "inventar": 6,
    "improvisar": 6, "generar": 6,
}

BLOOM_NOMBRES = {
    1: "Recordar", 2: "Comprender", 3: "Aplicar",
    4: "Analizar", 5: "Evaluar",    6: "Crear",
}

# ── Extractor de nivel desde texto libre ──────────────────────────────────────
# Cubre: "5to básico", "quinto básico", "5° básico", "5B", "1° medio", etc.
_NIVEL_REGEX = [
    (r"\b1[°º]?\s*b[aá]sico\b|\bprimero\s+b[aá]sico\b|\b1b\b|\bprimer\s+b[aá]sico\b",              "Primero Básico"),
    (r"\b2[°º]?\s*b[aá]sico\b|\bsegundo\s+b[aá]sico\b|\b2b\b|\b2do\s+b[aá]sico\b",                 "Segundo Básico"),
    (r"\b3[°º]?\s*b[aá]sico\b|\btercero\s+b[aá]sico\b|\b3b\b|\b3ro\s+b[aá]sico\b|\btercer\s+b[aá]sico\b", "Tercero Básico"),
    (r"\b4[°º]?\s*b[aá]sico\b|\bcuarto\s+b[aá]sico\b|\b4b\b|\b4to\s+b[aá]sico\b",                  "Cuarto Básico"),
    (r"\b5[°º]?\s*b[aá]sico\b|\bquinto\s+b[aá]sico\b|\b5b\b|\b5to\s+b[aá]sico\b",                  "Quinto Básico"),
    (r"\b6[°º]?\s*b[aá]sico\b|\bsexto\s+b[aá]sico\b|\b6b\b|\b6to\s+b[aá]sico\b",                   "Sexto Básico"),
    (r"\b7[°º]?\s*b[aá]sico\b|\bs[eé]ptimo\s+b[aá]sico\b|\b7b\b|\b7mo\s+b[aá]sico\b",              "Séptimo Básico"),
    (r"\b8[°º]?\s*b[aá]sico\b|\boctavo\s+b[aá]sico\b|\b8b\b|\b8vo\s+b[aá]sico\b",                  "Octavo Básico"),
    (r"\b1[°º]?\s*medio\b|\bprimero\s+medio\b|\b1m\b|\bprimer\s+medio\b",                            "Primero Medio"),
    (r"\b2[°º]?\s*medio\b|\bsegundo\s+medio\b|\b2m\b|\b2do\s+medio\b",                               "Segundo Medio"),
    (r"\b3[°º]?\s*medio\b|\btercero\s+medio\b|\b3m\b|\b3ro\s+medio\b|\btercer\s+medio\b",            "Tercero Medio"),
    (r"\b4[°º]?\s*medio\b|\bcuarto\s+medio\b|\b4m\b|\b4to\s+medio\b",                                "Cuarto Medio"),
]

def extraer_nivel_consulta(texto: str) -> str | None:
    """
    Extrae el nivel educativo mencionado en el texto.
    Retorna el nombre canónico (ej: 'Quinto Básico') o None si no encuentra.
    Sin llamadas externas — puro regex, cero latencia.
    """
    t = texto.lower()
    for patron, nivel in _NIVEL_REGEX:
        if re.search(patron, t):
            return nivel
    return None


def extraer_asignatura_consulta(texto: str) -> str | None:
    """
    Extrae el slug de asignatura mencionado en el texto.
    Retorna el slug (ej: 'musica') o None si no encuentra.
    """
    t = texto.lower()
    for nombre, slug in SLUGS_ASIGNATURA.items():
        if nombre in t or slug.replace("_", " ") in t:
            return slug
    # Alias comunes
    alias = {
        "mate":     "matematica",
        "historia": "ciencias_sociales",
        "geografía":"ciencias_sociales",
        "ciencias": "ciencias_naturales",
        "ed. física":"educacion_fisica",
        "física":   "educacion_fisica",
        "lenguaje": "lenguaje",
        "inglés":   "ingles",
        "música":   "musica",
        "artes":    "artes_visuales",
    }
    for term, slug in alias.items():
        if term in t:
            return slug
    return None

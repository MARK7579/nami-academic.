from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import google.generativeai as genai
import io
import sqlite3
import os
from pptx import Presentation
from pypdf import PdfReader

app = FastAPI()

# Permisos para que tu futura web pueda hablar con este servidor
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"], 
    allow_headers=["*"], 
)

# === SEGURIDAD APLICADA ===
# Ahora NAMI buscará la llave en la "nube" o en tu sistema, no en el texto.
# Si estás probando en tu computadora, puedes pegar tu llave temporalmente aquí 
# como 'fallback', pero recuerda borrarla antes de subir a GitHub.
API_KEY = os.environ.get("GEMINI_API_KEY", "TU_LLAVE_TEMPORAL_AQUI_SOLO_PARA_LOCAL") 

genai.configure(api_key=API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash')

# --- MODELOS DE DATOS ---
class ClaseData(BaseModel):
    nombre: str
    dia: str
    inicio: str
    fin: str

class EstudiarTemaRequest(BaseModel):
    curso: str
    tema: str
    tipo_output: str

# --- BASE DE DATOS LOCAL ---
def iniciar_memoria():
    conexion = sqlite3.connect('nami_memoria.db')
    cursor = conexion.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT,
            dia TEXT,
            inicio TEXT,
            fin TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS biblioteca (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            curso TEXT,
            tema TEXT,
            contenido TEXT
        )
    ''')
    conexion.commit()
    conexion.close()

iniciar_memoria()

def extraer_texto_pptx(file_content):
    prs = Presentation(io.BytesIO(file_content))
    return " ".join([shape.text for slide in prs.slides for shape in slide.shapes if hasattr(shape, "text")])

def extraer_texto_pdf(file_content):
    reader = PdfReader(io.BytesIO(file_content))
    return " ".join([page.extract_text() for page in reader.pages])

# --- ENDPOINTS DE HORARIO ---
@app.post("/agregar-clase")
async def agregar_clase(clase: ClaseData):
    conexion = sqlite3.connect('nami_memoria.db')
    cursor = conexion.cursor()
    cursor.execute('INSERT INTO clases (nombre, dia, inicio, fin) VALUES (?, ?, ?, ?)', 
                   (clase.nombre, clase.dia, clase.inicio, clase.fin))
    clase_id = cursor.lastrowid
    conexion.commit()
    conexion.close()
    return {"id": clase_id}

@app.get("/cargar-horario")
async def cargar_horario():
    conexion = sqlite3.connect('nami_memoria.db')
    cursor = conexion.cursor()
    cursor.execute('SELECT id, nombre, dia, inicio, fin FROM clases')
    clases = [{"id": row[0], "nombre": row[1], "dia": row[2], "inicio": row[3], "fin": row[4]} for row in cursor.fetchall()]
    conexion.close()
    return clases

@app.delete("/eliminar-clase/{clase_id}")
async def eliminar_clase(clase_id: int):
    conexion = sqlite3.connect('nami_memoria.db')
    cursor = conexion.cursor()
    cursor.execute('DELETE FROM clases WHERE id = ?', (clase_id,))
    conexion.commit()
    conexion.close()
    return {"mensaje": "Eliminada"}

# --- ENDPOINT IA: MODO ESTUDIO LIBRE ---
@app.post("/procesar-material")
async def procesar_material(file: UploadFile = File(...), tipo_output: str = "quiz"):
    try:
        content = await file.read()
        texto_extraido = extraer_texto_pptx(content) if file.filename.endswith(".pptx") else extraer_texto_pdf(content) if file.filename.endswith(".pdf") else ""
        if not texto_extraido: raise HTTPException(status_code=400, detail="Error de formato")
        instrucciones = {"quiz": "Crea un cuestionario de 5 preguntas de opción múltiple con respuestas...", "resumen": "Haz un resumen estructurado..."}
        prompt = f"{instrucciones.get(tipo_output, 'Resume esto')}: {texto_extraido[:10000]}"
        response = model.generate_content(prompt)
        return {"resultado": response.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- ENDPOINTS: GESTOR DE CONOCIMIENTO (SÍLABOS Y TEMAS) ---
@app.post("/subir-material-tema")
async def subir_material_tema(curso: str = Form(...), tema: str = Form(...), file: UploadFile = File(...)):
    try:
        content = await file.read()
        texto_extraido = extraer_texto_pptx(content) if file.filename.endswith(".pptx") else extraer_texto_pdf(content) if file.filename.endswith(".pdf") else ""
        if not texto_extraido: raise HTTPException(status_code=400, detail="El archivo está vacío o no es soportado.")

        conexion = sqlite3.connect('nami_memoria.db')
        cursor = conexion.cursor()
        cursor.execute('INSERT INTO biblioteca (curso, tema, contenido) VALUES (?, ?, ?)', (curso, tema, texto_extraido))
        conexion.commit()
        conexion.close()

        return {"mensaje": f"Material guardado con éxito en {curso} - {tema}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/obtener-temas/{curso}")
async def obtener_temas(curso: str):
    conexion = sqlite3.connect('nami_memoria.db')
    cursor = conexion.cursor()
    cursor.execute('SELECT DISTINCT tema FROM biblioteca WHERE curso = ?', (curso,))
    temas = [row[0] for row in cursor.fetchall()]
    conexion.close()
    return {"temas": temas}

@app.post("/estudiar-tema")
async def estudiar_tema(req: EstudiarTemaRequest):
    conexion = sqlite3.connect('nami_memoria.db')
    cursor = conexion.cursor()
    cursor.execute('SELECT contenido FROM biblioteca WHERE curso = ? AND tema = ?', (req.curso, req.tema))
    filas = cursor.fetchall()
    conexion.close()

    if not filas:
        raise HTTPException(status_code=404, detail="No hay material guardado en este tema.")

    texto_completo = " ".join([fila[0] for fila in filas])

    instrucciones = {
        "quiz": "Crea un cuestionario nivel universitario de 5 preguntas de opción múltiple (con respuestas al final) basado estrictamente en el siguiente texto de mi clase.",
        "resumen": "Haz un resumen estructurado, con puntos clave y viñetas, basado estrictamente en el siguiente texto de mi clase."
    }
    
    prompt = f"{instrucciones.get(req.tipo_output, 'Resume esto')}: {texto_completo[:15000]}"
    
    try:
        response = model.generate_content(prompt)
        return {"resultado": response.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Error conectando con la mente de NAMI: " + str(e)) 
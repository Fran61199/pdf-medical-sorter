# -*- coding: utf-8 -*-
import io
import re
import unicodedata
import zipfile
from pathlib import Path
from typing import List, Tuple, Set

import streamlit as st
from pypdf import PdfReader, PdfWriter

# ------------------ Config ------------------
STRICT_START = True        # solo cabecera (inicio de línea)
RELAXED_FALLBACK = False   # no escanear toda la página
CERT_NEAR_WINDOW = 200
TOP_LINES_K = 10
SHOW_DEBUG = False
# -------------------------------------------

# Patrones para "INFORME MÉDICO"
PAT_INFO = r"(?:informe\s+(?:del\s+)?(?:m[eé]dico(?:\s+ocupacional)?|examen\s+m[eé]dico))"

# Variantes frecuentes para "CERTIFICADO DE APTITUD (MÉDICO) (OCUPACIONAL)"
PAT_CERT_TITLES = [
    r"certificad[oa]\s+(?:m[eé]dic[ao](?:\s+ocupacional)?\s+)?(?:de\s+)?aptitud(?:\s+m[eé]dic[ao](?:\s+ocupacional)?)?"
]

# =============== Utilidades de texto ===============
def normalize_text_hard(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"[^a-zA-Z0-9]+", " ", s)
    s = s.lower().strip()
    s = re.sub(r"\s+", " ", s)
    return s

def first_nonempty_lines(text: str, k: int = TOP_LINES_K) -> List[str]:
    lines = (text or "").splitlines()
    out = []
    for ln in lines:
        n = normalize_text_hard(ln)
        if n:
            out.append(n)
            if len(out) >= k:
                break
    return out

def has_title_in_lines(text: str, pattern: str, strict_start: bool) -> bool:
    lines = first_nonempty_lines(text, k=TOP_LINES_K)
    # 1) línea por línea
    for ln in lines:
        if strict_start:
            if re.search(rf"^\s*{pattern}\b", ln):
                return True
        else:
            if re.search(pattern, ln):
                return True
    # 2) encabezado unido (maneja títulos partidos)
    head = normalize_text_hard(" ".join(lines))
    # quitar posibles encabezados previos que suelen ir antes
    head = re.sub(r"^(examen|periodico|evaluacion|ficha)\s+\w+\s+", "", head)
    if re.search(pattern, head):
        return True
    # 3) tolerar espaciado letra-a-letra: comparar sin espacios
    head_compact = head.replace(" ", "")
    pat_compact  = re.sub(r"\s+", "", pattern)
    return re.search(pat_compact, head_compact) is not None

def has_token_in_top_lines(text: str, token_pat: str, k: int = TOP_LINES_K) -> bool:
    for ln in first_nonempty_lines(text, k=k):
        if re.search(token_pat, ln):
            return True
    return False

def search_cert_proximity(full_norm: str) -> bool:
    has_mod = ("medic" in full_norm) or ("ocupacional" in full_norm)
    if not has_mod:
        return False
    for m in re.finditer(r"\bcertificad\w*\b", full_norm):
        start = m.end()
        window = full_norm[start:start + CERT_NEAR_WINDOW]
        if re.search(r"\baptitud\b", window):
            return True
    for m in re.finditer(r"\bcertificad\w*\b", full_norm):
        start = m.end()
        window = full_norm[start:start + CERT_NEAR_WINDOW]
        if re.search(r"(medic\w*|ocupacional)", window) and re.search(r"\baptitud\b", window):
            return True
    return False

# =============== Detección por página ===============
def is_cert_page(text: str) -> bool:
    for pat in PAT_CERT_TITLES:
        if has_title_in_lines(text, pat, strict_start=STRICT_START):
            return True
    if RELAXED_FALLBACK:
        full = normalize_text_hard(text or "")
        for pat in PAT_CERT_TITLES:
            if re.search(pat, full):
                return True
        if search_cert_proximity(full):
            return True

def is_info_page(text: str) -> bool:
    if has_title_in_lines(text, PAT_INFO, strict_start=STRICT_START):
        return True
    if RELAXED_FALLBACK:
        full = normalize_text_hard(text or "")
        if re.search(PAT_INFO, full):
            return True
    return False

def classify_pages(reader: PdfReader) -> Tuple[Set[int], Set[int], List[str]]:
    cert_set: Set[int] = set()
    info_set: Set[int] = set()
    labels_log: List[str] = []

    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        cert = is_cert_page(text)
        info = False if cert else is_info_page(text)  # prioridad CERT

        if cert:
            cert_set.add(i); labels_log.append(f"p.{i+1:03d} => CERTIFICADO")
        elif info:
            info_set.add(i); labels_log.append(f"p.{i+1:03d} => INFORME")
        else:
            labels_log.append(f"p.{i+1:03d} => OTROS")

    overlap = cert_set & info_set
    if overlap:
        info_set -= overlap
        labels_log.append(f"[ajuste] {len(overlap)} págs quitadas de INFORME por solape con CERT.")
    return cert_set, info_set, labels_log

# =============== Escritura PDFs en memoria ===============
def write_pdf_to_bytes(reader: PdfReader, idxs: List[int]) -> bytes:
    if not idxs:
        return b""
    w = PdfWriter()
    for i in idxs:
        w.add_page(reader.pages[i])
    bio = io.BytesIO()
    w.write(bio)
    bio.seek(0)
    return bio.read()

# =============== UI Streamlit ===============
st.set_page_config(page_title="Clasificar PDF Médico", page_icon="🩺", layout="centered")
st.title("🩺 Clasificar PDF: Certificado / Informe / Historia")
st.caption("Sube uno o varios PDF y genera los documentos clasificados para descargar.")

# 🔹 Ahora acepta múltiples archivos
uploaded_files = st.file_uploader("Adjunta uno o varios PDF", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    # ZIP maestro con todos los resultados de todos los PDFs
    zip_master_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_master_buffer, "w", zipfile.ZIP_DEFLATED) as zip_master:

        for idx, uploaded in enumerate(uploaded_files, start=1):
            try:
                file_bytes = uploaded.read()
                reader = PdfReader(io.BytesIO(file_bytes))
                total = len(reader.pages)

                cert_set, info_set, labels_log = classify_pages(reader)
                all_set = set(range(total))
                hist_set = all_set - cert_set - info_set

                cert = sorted(cert_set)
                info = sorted(info_set)
                hist = sorted(hist_set)

                pdf_cert   = write_pdf_to_bytes(reader, cert)
                pdf_info   = write_pdf_to_bytes(reader, info)
                pdf_hist   = write_pdf_to_bytes(reader, hist)
                pdf_legajo = write_pdf_to_bytes(reader, cert + info + hist)

                # Panel por archivo
                with st.expander(f"📄 {uploaded.name} — págs: {total} (Cert:{len(cert)} / Inf:{len(info)} / Hist:{len(hist)})", expanded=True):
                    col1, col2, col3, col4 = st.columns(4)
                    col1.metric("Certificado", len(cert))
                    col2.metric("Informe", len(info))
                    col3.metric("Historia", len(hist))
                    col4.metric("Total", total)

                    c1, c2 = st.columns(2)
                    with c1:
                        st.download_button("📄 Certificado", data=pdf_cert,
                                           file_name=f"{Path(uploaded.name).stem}_certificado.pdf",
                                           mime="application/pdf", disabled=(not pdf_cert), key=f"cert_{idx}")
                        st.download_button("📄 Historia Clínica", data=pdf_hist,
                                           file_name=f"{Path(uploaded.name).stem}_historia.pdf",
                                           mime="application/pdf", disabled=(not pdf_hist), key=f"hist_{idx}")
                    with c2:
                        st.download_button("📄 Informe Médico", data=pdf_info,
                                           file_name=f"{Path(uploaded.name).stem}_informe.pdf",
                                           mime="application/pdf", disabled=(not pdf_info), key=f"info_{idx}")
                        st.download_button("📦 Legajo (ordenado)", data=pdf_legajo,
                                           file_name=f"{Path(uploaded.name).stem}_legajo.pdf",
                                           mime="application/pdf", disabled=(not pdf_legajo), key=f"legajo_{idx}")

                # Agregar resultados de este archivo al ZIP maestro (en su carpeta)
                base = Path(uploaded.name).stem
                if pdf_cert:   zip_master.writestr(f"{base}/certificado.pdf", pdf_cert)
                if pdf_info:   zip_master.writestr(f"{base}/informe.pdf", pdf_info)
                if pdf_hist:   zip_master.writestr(f"{base}/historia.pdf", pdf_hist)
                if pdf_legajo: zip_master.writestr(f"{base}/legajo.pdf", pdf_legajo)
                if SHOW_DEBUG:
                    zip_master.writestr(f"{base}/debug_deteccion.txt", "\n".join(labels_log))

            except Exception as e:
                st.error(f"❌ {uploaded.name}: {e}")

    # Botón para descargar TODO junto
    zip_master_buffer.seek(0)
    st.download_button("🗜️ Descargar TODO (todos los PDFs) .zip",
                       data=zip_master_buffer.getvalue(),
                       file_name="clasificados_todos.zip",
                       mime="application/zip")
else:
    st.caption("Formatos soportados: .pdf — El procesamiento ocurre en memoria (sin guardar archivos en disco).")
# =============== FOOTER ===============
st.markdown("---")
st.markdown(
    "<p style='text-align:center; color:gray; font-size:14px;'>"
    "Creado por: <b>Equipo de Customer Success</b>"
    "</p>",
    unsafe_allow_html=True
)

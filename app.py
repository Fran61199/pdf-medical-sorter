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
STRICT_START = False
RELAXED_FALLBACK = True
CERT_NEAR_WINDOW = 200
TOP_LINES_K = 4
SHOW_DEBUG = False
# -------------------------------------------

# Patrones para "INFORME MÉDICO"
PAT_INFO = r"(?:informe\s+(?:del\s+)?(?:m[eé]dico(?:\s+ocupacional)?|examen\s+m[eé]dico))"
PAT_CERT_TITLES = [
    r"certificad[oa]\s+de\s+aptitud\s+m[eé]dic[ao]\s+ocupacional",
    r"certificad[oa]\s+m[eé]dic[ao]\s+ocupacional\s+de\s+aptitud",
    r"certificad[oa]\s+de\s+aptitud\s+ocupacional",
    r"certificad[oa]\s+ocupacional\s+de\s+aptitud",
    r"certificad[oa]\s+m[eé]dic[ao]\s+de\s+aptitud",
    r"certificad[oa]\s+de\s+aptitud\s+m[eé]dic[ao]",
    r"certificad[oa]\s+(?:de\s+)?aptitud",
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
    for ln in first_nonempty_lines(text, k=TOP_LINES_K):
        if strict_start:
            if re.search(rf"^\s*{pattern}\b", ln):
                return True
        else:
            if re.search(pattern, ln):
                return True
    return False

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

    full_norm = normalize_text_hard(text or "")
    aptitud_top = has_token_in_top_lines(text, r"\baptitud\b", k=TOP_LINES_K)
    has_cert_word = ("certificad" in full_norm)
    has_context = ("medic" in full_norm) or ("ocupacional" in full_norm)
    if aptitud_top and (has_cert_word or has_context):
        return True

    return False

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
        info = False if cert else is_info_page(text)
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
st.caption("Sube un PDF y genera los documentos clasificados para descargar.")

uploaded = st.file_uploader("Adjunta un PDF", type=["pdf"])

if uploaded is not None:
    try:
        file_bytes = uploaded.read()
        reader = PdfReader(io.BytesIO(file_bytes))
        total = len(reader.pages)

        st.info(f"👀 Páginas detectadas: **{total}**")

        cert_set, info_set, labels_log = classify_pages(reader)
        all_set = set(range(total))
        hist_set = all_set - cert_set - info_set

        cert = sorted(cert_set)
        info = sorted(info_set)
        hist = sorted(hist_set)

        pdf_cert = write_pdf_to_bytes(reader, cert)
        pdf_info = write_pdf_to_bytes(reader, info)
        pdf_hist = write_pdf_to_bytes(reader, hist)
        pdf_legajo = write_pdf_to_bytes(reader, cert + info + hist)

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Certificado", len(cert))
        col2.metric("Informe", len(info))
        col3.metric("Historia", len(hist))
        col4.metric("Total", total)

        st.subheader("⬇️ Descargas")
        c1, c2 = st.columns(2)
        with c1:
            if pdf_cert:
                st.download_button("📄 Certificado de Aptitud", data=pdf_cert,
                                   file_name="certificado_de_aptitud_all.pdf", mime="application/pdf")
            else:
                st.button("📄 Certificado de Aptitud (sin páginas)", disabled=True)

            if pdf_hist:
                st.download_button("📄 Historia Clínica", data=pdf_hist,
                                   file_name="historia_clinica_all.pdf", mime="application/pdf")
            else:
                st.button("📄 Historia Clínica (sin páginas)", disabled=True)

        with c2:
            if pdf_info:
                st.download_button("📄 Informe Médico", data=pdf_info,
                                   file_name="informe_medico_all.pdf", mime="application/pdf")
            else:
                st.button("📄 Informe Médico (sin páginas)", disabled=True)

            if pdf_legajo:
                st.download_button("📦 Legajo (ordenado)", data=pdf_legajo,
                                   file_name="legajo.pdf", mime="application/pdf")
            else:
                st.button("📦 Legajo (sin páginas)", disabled=True)

        with io.BytesIO() as zip_buffer:
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as z:
                if pdf_cert:
                    z.writestr("certificado_de_aptitud_all.pdf", pdf_cert)
                if pdf_info:
                    z.writestr("informe_medico_all.pdf", pdf_info)
                if pdf_hist:
                    z.writestr("historia_clinica_all.pdf", pdf_hist)
                if pdf_legajo:
                    z.writestr("legajo.pdf", pdf_legajo)
                if SHOW_DEBUG:
                    z.writestr("debug/deteccion_por_pagina.txt", "\n".join(labels_log))
            zip_buffer.seek(0)
            st.download_button("🗜️ Descargar TODO (.zip)", data=zip_buffer.getvalue(),
                               file_name="clasificados.zip", mime="application/zip")

        if SHOW_DEBUG:
            st.divider()
            st.subheader("🔎 Debug (etiquetas por página)")
            st.code("\n".join(labels_log), language="text")

        if (len(cert) + len(info)) == 0:
            st.warning("No se detectaron títulos. Si el PDF es escaneado (imágenes), necesitas OCR.")

    except Exception as e:
        st.error(f"Error leyendo el PDF: {e}")
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

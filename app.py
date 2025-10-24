# -*- coding: utf-8 -*-
"""
Clasifica POR PÁGINA y genera 4 PDFs:
1) certificado_de_aptitud_all.pdf  → páginas detectadas como CERTIFICADO
2) informe_médico_all.pdf          → páginas detectadas como INFORME
3) historia_clínica_all.pdf        → resto (no CERT ni INFORME)
4) legajo.pdf                      → CERT → INFORME → HISTORIA

Uso:
    pip install pypdf
    python app.py
    # o: python app.py pdf/MiArchivo.pdf
"""

import re
import sys
import unicodedata
from pathlib import Path
from typing import List, Tuple, Set
from pypdf import PdfReader, PdfWriter

# ===== Config =====
INPUT_DIR = Path("pdf")
STRICT_START = False
RELAXED_FALLBACK = True
DEBUG = False
CERT_NEAR_WINDOW = 200
TOP_LINES_K = 8  # cuántas primeras líneas revisar
# ==================

PAT_INFO = r"(?:informe\s+m[eé]dico(?:\s+ocupacional)?)"
PAT_CERT_TITLES = [
    r"certificad[oa]\s+de\s+aptitud\s+m[eé]dic[ao]\s+ocupacional",
    r"certificad[oa]\s+m[eé]dic[ao]\s+ocupacional\s+de\s+aptitud",
    r"certificad[oa]\s+de\s+aptitud\s+ocupacional",
    r"certificad[oa]\s+ocupacional\s+de\s+aptitud",
    r"certificad[oa]\s+m[eé]dic[ao]\s+de\s+aptitud",
    r"certificad[oa]\s+de\s+aptitud\s+m[eé]dic[ao]",
    r"certificad[oa]\s+(?:de\s+)?aptitud",
]

# ================== FUNCIONES ==================

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

    # 3) Regla específica: "APTITUD" en primeras líneas + contexto certificado
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

def write_pdf(reader: PdfReader, idxs: List[int], out_path: Path) -> None:
    if not idxs:
        return
    w = PdfWriter()
    for i in idxs:
        w.add_page(reader.pages[i])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        w.write(f)

def process(input_path: Path) -> None:
    global DEBUG
    if not input_path.exists():
        print(f"❌ No existe: {input_path}"); return
    print(f"\n📄 Procesando: {input_path.name}")
    reader = PdfReader(str(input_path))
    total = len(reader.pages)
    if total == 0: print("   (PDF sin páginas)"); return

    base = input_path.stem
    outroot = Path("output") / base
    dbg_dir = outroot / "debug"

    cert_set, info_set, labels_log = classify_pages(reader)
    all_set = set(range(total))
    hist_set = all_set - cert_set - info_set

    cert = sorted(cert_set); info = sorted(info_set); hist = sorted(hist_set)

    if len(cert) == 0:
        DEBUG = True

    write_pdf(reader, cert, outroot / "certificado de aptitud" / "certificado_de_aptitud_all.pdf")
    write_pdf(reader, info, outroot / "informe médico" / "informe_médico_all.pdf")
    write_pdf(reader, hist, outroot / "historia clínica" / "historia_clínica_all.pdf")

    legajo_order = cert + info + hist
    write_pdf(reader, legajo_order, outroot / "legajo" / "legajo.pdf")

    if DEBUG:
        dbg_dir.mkdir(parents=True, exist_ok=True)
        (dbg_dir / "deteccion_por_pagina.txt").write_text("\n".join(labels_log), encoding="utf-8")

    print("   ✅ Listo.")
    print(f"     Páginas totales          : {total}")
    print(f"     CERTIFICADO (páginas)    : {len(cert)}")
    print(f"     INFORME MÉDICO (páginas) : {len(info)}")
    print(f"     HISTORIA CLÍNICA (pág.)  : {len(hist)}")
    print(f"     Salida en                : {outroot}")
    if DEBUG:
        print(f"     Debug                    : {dbg_dir/'deteccion_por_pagina.txt'}")

def main():
    INPUT_DIR.mkdir(exist_ok=True)
    if len(sys.argv) > 1:
        pdf_path = Path(sys.argv[1])
    else:
        pdfs = sorted(INPUT_DIR.glob("*.pdf"))
        if not pdfs:
            print("⚠️  Pega un PDF en la carpeta 'pdf' o pásalo como argumento."); return
        pdf_path = pdfs[0]
    process(pdf_path)

if __name__ == "__main__":
    main()

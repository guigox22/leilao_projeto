import os
import re
import io
import zipfile
from flask import Flask, render_template, request, jsonify, send_file

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 150 * 1024 * 1024  # 150 MB

# ─── EXTRAÇÃO DE TEXTO ───────────────────────────────────────────────────────

def extract_text_from_bytes(data, filename=''):
    """Extrai texto de PDF, DOCX ou TXT a partir de bytes."""
    name = filename.lower()

    if name.endswith('.pdf') or (not name and data[:4] == b'%PDF'):
        try:
            import pdfplumber
            pages = []
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                for page in pdf.pages:
                    try:
                        txt = page.extract_text()
                        if txt:
                            pages.append(txt)
                    except Exception:
                        continue
            return '\n'.join(pages)
        except Exception:
            return ''

    elif name.endswith('.docx'):
        from docx import Document
        doc = Document(io.BytesIO(data))
        return '\n'.join(p.text for p in doc.paragraphs)

    else:
        return data.decode('utf-8', errors='ignore')


def extract_text(file_storage):
    data = file_storage.read()
    return extract_text_from_bytes(data, file_storage.filename or '')


def split_pdf_bytes(data, pages_per_chunk=150):
    """Divide um PDF em chunks e retorna lista de bytes."""
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(io.BytesIO(data))
    total  = len(reader.pages)
    chunks = []
    for start in range(0, total, pages_per_chunk):
        end = min(start + pages_per_chunk, total)
        writer = PdfWriter()
        for i in range(start, end):
            writer.add_page(reader.pages[i])
        buf = io.BytesIO()
        writer.write(buf)
        chunks.append(buf.getvalue())
    return chunks, total


# ─── PARSER: RELAÇÃO DE LOTES ─────────────────────────────────────────────────

def parse_relacao(text):
    mapa = {}
    blocos = re.split(r'(?=Lote\s+N[°º\.o]*\s*:\s*\d+)', text, flags=re.IGNORECASE)

    for bloco in blocos:
        m = re.search(r'Lote\s+N[°º\.o]*\s*:\s*(\d+)', bloco, re.IGNORECASE)
        if not m:
            continue
        num = str(int(m.group(1)))

        m_min  = re.search(r'Preço\s*Mínimo\s*\(R\$\)\s*:\s*([\d\.]+,\d{2})', bloco, re.IGNORECASE)
        m_av   = re.search(r'Avaliado\s+em\s*\(R\$\)\s*:\s*([\d\.]+,\d{2})', bloco, re.IGNORECASE)
        m_tipo = re.search(r'Tipo\s+de\s+Lote\s*:\s*([^\n\r]+)', bloco, re.IGNORECASE)

        preco_min = f"R$ {m_min.group(1)}" if m_min else "Não encontrado"
        preco_av  = f"R$ {m_av.group(1)}"  if m_av  else "Não encontrado"
        tipo      = m_tipo.group(1).strip() if m_tipo else ""

        desc = bloco
        for pat in [
            r'Lote\s+N[°º\.o]*\s*:\s*\d+',
            r'Tipo\s+de\s+Lote\s*:[^\n]+',
            r'Preço\s*Mínimo\s*\(R\$\)\s*:[^\n]+',
            r'Avaliado\s+em\s*\(R\$\)\s*:[^\n]+',
            r'UA:\s*\d+.*?(?:\n|$)',
            r'Processo de Licitação:.*?(?:\n|$)',
            r'Relatório.*?(?:\n|$)',
            r'Edital.*?(?:\n|$)',
            r'MINISTÉRIO.*?(?:\n|$)',
            r'SECRETARIA.*?(?:\n|$)',
            r'FEDERAL.*?(?:\n|$)',
            r'Data:.*?(?:\n|$)',
            r'Recinto Armazenador.*?(?:\n|$)',
            r'Quant\s+Un\.?\s*Med\.?.*?(?:\n|$)',
            r'Marca/Modelo.*?(?:\n|$)',
            r'Complemento Adicional.*?(?:\n|$)',
            r'Depósito\s+Próprio.*?(?:\n|$)',
            r'CLIA\s*-[^\n]*(?:\n|$)',
            r'Fiel Depositário.*?(?:\n|$)',
            r'Página\s+\d+.*?(?:\n|$)',
            r'ADM.*?(?:\n|$)',
            r'DMA.*?(?:\n|$)',
            r'^\s*/\s*/\s*(?:\n|$)',
        ]:
            desc = re.sub(pat, ' ', desc, flags=re.IGNORECASE | re.MULTILINE)

        desc = re.sub(r'\s{2,}', ' ', desc).strip(' ,;:/\n\r-')
        if len(desc) > 350:
            desc = desc[:350] + '…'

        mapa[num] = {
            'preco_minimo':   preco_min,
            'preco_avaliado': preco_av,
            'tipo':           tipo,
            'descricao':      desc or tipo or 'Ver edital completo.'
        }

    return mapa


# ─── PARSER: PROPOSTAS E LANCES ──────────────────────────────────────────────

def parse_lances(text):
    mapa = {}

    matches = re.findall(
        r'Lote\s*:\s*(\d+)[^\n]*\n[^\n]*\nValor\s+de\s+Arrematação\s+([\d\.]+,\d{2}|-)',
        text, re.IGNORECASE
    )
    for num, valor in matches:
        key = str(int(num))
        mapa[key] = 'Não arrematado' if valor.strip() == '-' else f'R$ {valor}'

    if not mapa:
        all_vals  = re.findall(r'Valor\s+de\s+Arrematação\s+([\d\.]+,\d{2}|-)', text, re.IGNORECASE)
        all_lotes = re.findall(r'Lote\s*:\s*(\d+)', text, re.IGNORECASE)
        for lote, val in zip(all_lotes, all_vals):
            key = str(int(lote))
            mapa[key] = 'Não arrematado' if val.strip() == '-' else f'R$ {val}'

    return mapa


# ─── PROCESSAMENTO COM SPLIT AUTOMÁTICO ──────────────────────────────────────

def process_large_pdf(data, filename, parser_fn, pages_per_chunk=150):
    """
    Para PDFs grandes: divide em chunks, processa cada um e combina os resultados.
    Retorna (mapa_combinado, total_paginas).
    """
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        total_pages = len(reader.pages)
    except Exception:
        # Se não conseguir ler com pypdf, tenta direto
        text = extract_text_from_bytes(data, filename)
        return parser_fn(text), 0

    if total_pages <= pages_per_chunk:
        # PDF pequeno — processa direto
        text = extract_text_from_bytes(data, filename)
        return parser_fn(text), total_pages

    # PDF grande — divide e processa em chunks
    chunks, total = split_pdf_bytes(data, pages_per_chunk)
    mapa_final = {}
    for chunk_data in chunks:
        text  = extract_text_from_bytes(chunk_data, filename)
        chunk_mapa = parser_fn(text)
        mapa_final.update(chunk_mapa)  # lotes de chunks posteriores sobrescrevem (correto)

    return mapa_final, total


# ─── ROTAS ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/extrair_pdf', methods=['POST'])
def extrair_pdf():
    if 'file' not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400
    f = request.files['file']
    if not f.filename.lower().endswith('.pdf'):
        return jsonify({"error": "Envie um arquivo PDF (.pdf)"}), 400

    text = extract_text(f)
    if not text.strip():
        return jsonify({"error": "Não foi possível extrair texto deste PDF."}), 500

    return jsonify({"texto": text, "linhas": text.count('\n') + 1})


@app.route('/dividir_pdf', methods=['POST'])
def dividir_pdf_route():
    """Divide um PDF grande em partes menores e retorna um ZIP para download."""
    if 'file' not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400
    f    = request.files['file']
    data = f.read()
    nome = os.path.splitext(f.filename or 'arquivo')[0]

    try:
        pgs_str = request.form.get('paginas', '150')
        pgs_por_parte = int(pgs_str)
    except ValueError:
        pgs_por_parte = 150

    try:
        chunks, total = split_pdf_bytes(data, pgs_por_parte)
    except Exception as e:
        return jsonify({"error": f"Erro ao dividir: {str(e)}"}), 500

    if len(chunks) == 1:
        return jsonify({"error": f"O PDF tem apenas {total} páginas — não precisa dividir!"}), 400

    # Criar ZIP com todas as partes
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i, chunk in enumerate(chunks):
            zf.writestr(f"{nome}_parte{i+1:02d}.pdf", chunk)
    zip_buf.seek(0)

    return send_file(
        zip_buf,
        mimetype='application/zip',
        as_attachment=True,
        download_name=f"{nome}_dividido.zip"
    )


@app.route('/upload', methods=['POST'])
def upload_edital():
    if 'file' not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400
    f    = request.files['file']
    data = f.read()

    try:
        mapa, total_pages = process_large_pdf(data, f.filename or '', parse_relacao)

        if not mapa:
            return jsonify({"error": "Nenhum lote encontrado. Verifique se o arquivo é uma Relação de Lotes."}), 400

        resultado = [
            {
                "lote":           num,
                "tipo":           d['tipo'],
                "preco_minimo":   d['preco_minimo'],
                "preco_avaliado": d['preco_avaliado'],
                "descricao":      d['descricao'],
            }
            for num, d in mapa.items()
        ]
        resultado.sort(key=lambda x: int(x['lote']) if x['lote'].isdigit() else 999)
        return jsonify(resultado)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/upload_historico', methods=['POST'])
def upload_historico():
    if 'file_relacao' not in request.files or 'file_lances' not in request.files:
        return jsonify({"error": "Envie os dois arquivos: Relação de Lotes e Propostas/Lances."}), 400

    f_relacao = request.files['file_relacao']
    f_lances  = request.files['file_lances']

    try:
        data_relacao = f_relacao.read()
        data_lances  = f_lances.read()

        mapa_relacao, _ = process_large_pdf(data_relacao, f_relacao.filename or '', parse_relacao)
        mapa_lances,  _ = process_large_pdf(data_lances,  f_lances.filename  or '', parse_lances)

        if not mapa_relacao:
            return jsonify({"error": "Nenhum lote encontrado na Relação de Lotes."}), 400

        resultado = []
        for num, d in mapa_relacao.items():
            arr = mapa_lances.get(num, 'Sem informação')
            resultado.append({
                "lote":             num,
                "tipo":             d['tipo'],
                "preco_minimo":     d['preco_minimo'],
                "preco_avaliado":   d['preco_avaliado'],
                "valor_arrematado": arr,
                "descricao":        d['descricao'],
            })

        resultado.sort(key=lambda x: int(x['lote']) if x['lote'].isdigit() else 999)
        return jsonify(resultado)

    except Exception as e:
        return jsonify({"error": f"Erro ao processar: {str(e)}"}), 500


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True, use_reloader=False)
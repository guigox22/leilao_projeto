import os
import re
import io
import zipfile
from flask import Flask, render_template, request, jsonify, send_file

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 150 * 1024 * 1024  # 150 MB

# ─── EXTRAÇÃO DE TEXTO DIRETA (ULTRA RÁPIDA E BAIXO CONSUMO) ─────────────────

def extract_text(file_storage):
    """Extrai o texto do arquivo diretamente da stream sem duplicar em memória"""
    data = file_storage.read()
    filename = (file_storage.filename or '').lower()

    if filename.endswith('.pdf') or (data[:4] == b'%PDF'):
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            pages = []
            for page in reader.pages:
                txt = page.extract_text()
                if txt:
                    pages.append(txt)
            return '\n'.join(pages)
        except Exception:
            return ''
    elif filename.endswith('.docx'):
        from docx import Document
        doc = Document(io.BytesIO(data))
        return '\n'.join(p.text for p in doc.paragraphs)
    else:
        return data.decode('utf-8', errors='ignore')


def split_pdf_bytes(data, pages_per_chunk=150):
    """Mantido puramente para a aba de Divisão manual de PDFs"""
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
    blocos = re.split(r'(?=Lote\s*(?:N[°º\.o]*)?\s*:\s*\d+)', text, flags=re.IGNORECASE)

    for bloco in blocos:
        if not bloco.strip():
            continue
        m = re.search(r'Lote\s*(?:N[°º\.o]*)?\s*:\s*(\d+)', bloco, re.IGNORECASE)
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
            r'Lote\s*(?:N[°º\.o]*)?\s*:\s*\d+',
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
            r'DRF.*?(?:\n|$)',
            r'ARF.*?(?:\n|$)',
            r'TECA.*?(?:\n|$)',
            r'DEPOSITO.*?(?:\n|$)',
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


# ─── PARSER: PROPOSTAS E LANCES (CAPTURADOR MULTILINHA BLINDADO) ──────────────

def parse_lances(text):
    mapa = {}
    # Divide o arquivo de lances em grandes caixas por lote
    blocos = re.split(r'(?=Lote\s*:\s*\d+)', text, flags=re.IGNORECASE)

    for bloco in blocos:
        if not bloco.strip():
            continue
        m_lote = re.search(r'Lote\s*:\s*(\d+)', bloco, re.IGNORECASE)
        if not m_lote:
            continue
        num = str(int(m_lote.group(1)))

        # Captura o valor mesmo que existam quebras de linha entre o termo e o preço
        m_val = re.search(r'Valor\s+de\s+Arrematação[\s\S]{0,150}?(\d{1,3}(?:\.\d{3})*,\d{2})', bloco, re.IGNORECASE)
        
        if m_val:
            mapa[num] = f"R$ {m_val.group(1)}"
        else:
            if '-' in bloco and 'Encerrado' in bloco:
                mapa[num] = 'Não arrematado'
            else:
                # Fallback secundário por proximidade monetária
                valores_moeda = re.findall(r'\d{1,3}(?:\.\d{3})*,\d{2}', bloco)
                if valores_moeda:
                    mapa[num] = f"R$ {valores_moeda[0]}"
                else:
                    mapa[num] = 'Não arrematado'
    return mapa


# ─── ROTAS FLASK ─────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/extrair_pdf', methods=['POST'])
def extrair_pdf():
    if 'file' not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400
    f = request.files['file']
    text = extract_text(f)
    if not text.strip():
        return jsonify({"error": "Não foi possível extrair texto deste PDF."}), 500
    return jsonify({"texto": text, "linhas": text.count('\n') + 1})


@app.route('/dividir_pdf', methods=['POST'])
def dividir_pdf_route():
    if 'file' not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400
    f    = request.files['file']
    data = f.read()
    nome = os.path.splitext(f.filename or 'arquivo')[0]

    try:
        pgs_por_parte = int(request.form.get('paginas', '150'))
        chunks, total = split_pdf_bytes(data, pgs_por_parte)
    except Exception as e:
        return jsonify({"error": f"Erro ao dividir: {str(e)}"}), 500

    if len(chunks) == 1:
        return jsonify({"error": f"O PDF tem apenas {total} páginas — não precisa dividir!"}), 400

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i, chunk in enumerate(chunks):
            zf.writestr(f"{nome}_parte{i+1:02d}.pdf", chunk)
    zip_buf.seek(0)

    return send_file(zip_buf, mimetype='application/zip', as_attachment=True, download_name=f"{nome}_dividido.zip")


@app.route('/upload', methods=['POST'])
def upload_edital():
    if 'file' not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400
    f = request.files['file']

    try:
        text = extract_text(f)
        mapa = parse_relacao(text)

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
        # Extração direta e linear (Super leve e veloz)
        text_relacao = extract_text(f_relacao)
        text_lances  = extract_text(f_lances)

        mapa_relacao = parse_relacao(text_relacao)
        mapa_lances  = parse_lances(text_lances)

        if not mapa_relacao:
            return jsonify({"error": "Nenhum lote encontrado na Relação de Lotes."}), 400

        resultado = []
        for num, d in mapa_relacao.items():
            arr = mapa_lances.get(num, 'Não arrematado')
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
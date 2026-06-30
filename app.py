import os
import re
import io
import zipfile
from flask import Flask, render_template, request, jsonify, send_file

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 150 * 1024 * 1024  # Limite seguro de 150 MB

# ─── 1. INTERCEPTADOR GLOBAL DE ERROS (BLINDAGEM CONTRA ERRO DE JSON) ────────

@app.errorhandler(Exception)
def handle_exception(e):
    """
    Captura QUALQUER erro interno do servidor ou framework e força o retorno 
    em formato JSON limpo. Isso impede o navegador de receber páginas HTML,
    eliminando de vez o erro 'Unexpected token <'.
    """
    if hasattr(e, 'code') and hasattr(e, 'description'):
        return jsonify({"error": f"Erro {e.code}: {e.description}"}), e.code
    return jsonify({"error": f"Erro interno de processamento: {str(e)}"}), 500


# ─── 2. EXTRAÇÃO DE TEXTO DIRETA (ULTRA LEVE E LIVRE DE CRASHES) ──────────────

def extract_text(file_storage):
    """Lê o arquivo diretamente de forma sequencial sem duplicar dados na RAM"""
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
    """Mantido puramente para dar suporte à aba de Divisão manual de PDFs"""
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


# ─── 3. PARSER DA RELAÇÃO DE LOTES (MÉTODO DE JANELA EXPANDIDA) ───────────────

def parse_relacao(text):
    """
    Usa busca por proximidade em uma janela de contexto.
    Resolve problemas onde o Preço Mínimo é extraído antes ou depois do número do Lote.
    """
    mapa = {}
    # Encontra os pontos exatos onde cada marcador de Lote começa no documento
    iterator = re.finditer(r'Lote\s*(?:N[°º\.o]*)?\s*:\s*(\d+)', text, re.IGNORECASE)
    matches = list(iterator)

    for i, match in enumerate(matches):
        num = str(int(match.group(1)))
        start_pos = match.start()

        # Abre uma janela flexível ao redor do marcador do Lote para capturar dados flutuantes
        window_start = max(0, start_pos - 600)  # Pega até 600 caracteres antes (caso valores subam)
        if i + 1 < len(matches):
            window_end = matches[i+1].start()   # Limita até o início do próximo lote
        else:
            window_end = len(text)

        window_end = max(window_end, start_pos + 2000)
        window_end = min(window_end, len(text))

        bloco_completo = text[window_start:window_end]

        # Captura de Valores
        m_min  = re.search(r'Preço\s*Mínimo\s*\(R\$\)\s*:\s*([\d\.]+,\d{2})', bloco_completo, re.IGNORECASE)
        m_av   = re.search(r'Avaliado\s+(?:em\s*)?\(R\$\)\s*:\s*([\d\.]+,\d{2})', bloco_completo, re.IGNORECASE)
        m_tipo = re.search(r'Tipo\s+de\s+Lote\s*:\s*([^\n\r]+)', bloco_completo, re.IGNORECASE)

        preco_min = f"R$ {m_min.group(1)}" if m_min else "Não encontrado"
        preco_av  = f"R$ {m_av.group(1)}"  if m_av  else "Não encontrado"
        tipo      = m_tipo.group(1).strip() if m_tipo else ""

        # Limpeza cirúrgica da descrição pegando do número do lote para a frente
        desc_bloco = text[start_pos:window_end]
        desc = desc_bloco
        for pat in [
            r'Lote\s*(?:N[°º\.o]*)?\s*:\s*\d+',
            r'Tipo\s+de\s+Lote\s*:[^\n]+',
            r'Preço\s*Mínimo\s*\(R\$\)\s*:[^\n]+',
            r'Avaliado\s+(?:em\s*)?\(R\$\)\s*:[^\n]+',
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


# ─── 4. PARSER DE PROPOSTAS E LANCES (BUSCA INTEGRAL SEM LIMITE DE LINHA) ─────

def parse_lances(text):
    """
    Recorta o arquivo de lances em blocos independentes por lote.
    Mapeia o valor de arrematação ignorando quebras de linha intermediárias.
    """
    mapa = {}
    blocos = re.split(r'(?=Lote\s*:\s*\d+)', text, flags=re.IGNORECASE)

    for bloco in blocos:
        if not bloco.strip():
            continue
        m_lote = re.search(r'Lote\s*:\s*(\d+)', bloco, re.IGNORECASE)
        if not m_lote:
            continue
        num = str(int(m_lote.group(1)))

        # Captura o valor pulando qualquer linha criada pelo PDF (ex: Estado do Lote, Arrematante)
        m_val = re.search(r'Valor\s+de\s+Arrematação\s*[\s\S]{0,250}?(\d{1,3}(?:\.\d{3})*,\d{2})', bloco, re.IGNORECASE)
        
        if m_val:
            mapa[num] = f"R$ {m_val.group(1)}"
        else:
            if 'encerrado' in bloco.lower() or 'não arrematado' in bloco.lower() or '-' in bloco:
                mapa[num] = 'Não arrematado'
            else:
                mapa[num] = 'Sem informação / Lote ativo'
    return mapa


# ─── 5. ROTAS FLASK ──────────────────────────────────────────────────────────

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
            return jsonify({"error": "Nenhum lote encontrado. Certifique-se de que é um PDF válido de Relação de Lotes."}), 400

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
        return jsonify({"error": "Por favor, selecione ambos os arquivos requeridos."}), 400

    f_relacao = request.files['file_relacao']
    f_lances  = request.files['file_lances']

    try:
        text_relacao = extract_text(f_relacao)
        text_lances  = extract_text(f_lances)

        mapa_relacao = parse_relacao(text_relacao)
        mapa_lances  = parse_lances(text_lances)

        if not mapa_relacao:
            return jsonify({"error": "Nenhum lote mapeado na Relação de Lotes."}), 400

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
        return jsonify({"error": f"Falha no processamento: {str(e)}"}), 500


# ─── 6. PARSER DE EXTRATO DE LEILÃO (LOTE · ARREMATANTE · VALOR) ─────────────

def parse_extrato(text):
    """
    Lê o Extrato do Leilão da Receita Federal.
    Formato de cada linha: <Lote>  <CNPJ/CPF>  [Valor]  <Arrematante>  [Valor]
    Casos tratados:
      - Valor ANTES do nome (PDF quebra a linha assim em ~30% dos casos)
      - Valor DEPOIS do nome (caso mais comum)
      - Lote Excluído
      - Nome do arrematante continua na próxima linha
    """
    linhas = text.splitlines()
    resultado = []
    i = 0

    pat_lote  = re.compile(r'^\s*(\d{1,4})\s+')
    pat_cnpj  = re.compile(
        r'\d{2}\.?\d{3}\.?\d{3}/\d{4}-\d{2}'   # CNPJ formatado
        r'|\*{3}\.\d{3}\.\d{3}-\*{2}'            # CPF mascarado
        r'|\b\d{11}\b'                             # CPF numérico puro
    )
    pat_valor = re.compile(r'\b(\d{1,3}(?:\.\d{3})*,\d{2})\b')
    pat_excl  = re.compile(r'Lote\s+Exclu[íi]do', re.IGNORECASE)
    pat_skip  = re.compile(
        r'^(Lote\b|CNPJ|Arrematante|Valor\s+Arr|Relat|Edital|MINIST|SECRET|FEDERAL|P[áa]gina\s+\d|Data:)',
        re.IGNORECASE
    )

    while i < len(linhas):
        linha = linhas[i].strip()
        i += 1

        if not linha:
            continue

        m_lote = pat_lote.match(linha)
        if not m_lote:
            continue

        num = str(int(m_lote.group(1)))

        # Lote excluído
        if pat_excl.search(linha):
            resultado.append({"lote": num, "arrematante": "Lote Excluído", "valor": "—"})
            continue

        # Precisa ter CNPJ/CPF para ser linha de dados
        if not pat_cnpj.search(linha):
            continue

        # Remove o número do lote e o CNPJ/CPF; fica só nome + valor(es)
        resto = linha[m_lote.end():]
        resto = pat_cnpj.sub(' ', resto)

        # Coleta todos os valores numéricos encontrados no resto
        valores_encontrados = pat_valor.findall(resto)

        # Remove os valores do texto para isolar o nome
        nome_limpo = pat_valor.sub(' ', resto).strip(' ,;-/')
        nome_limpo = re.sub(r'\s{2,}', ' ', nome_limpo).strip()

        # Verifica se o nome continua na linha seguinte
        if i < len(linhas):
            prox = linhas[i].strip()
            # Linha de continuação: não começa com número de lote, sem CNPJ e não é cabeçalho
            if (prox
                    and not pat_lote.match(prox)
                    and not pat_cnpj.search(prox)
                    and not pat_skip.match(prox)
                    and not pat_excl.search(prox)
                    and len(prox) > 2):
                # Complemento do nome
                complemento = pat_valor.sub(' ', prox).strip()
                complemento = re.sub(r'\s{2,}', ' ', complemento).strip()
                if complemento:
                    nome_limpo = (nome_limpo + ' ' + complemento).strip()
                i += 1  # consome a linha de continuação

        # Escolhe o valor: pega o MAIOR entre os encontrados
        # (evita pegar IDs numéricos menores como valor)
        valor_str = "—"
        if valores_encontrados:
            def to_float(v):
                return float(v.replace('.', '').replace(',', '.'))
            maior = max(valores_encontrados, key=to_float)
            valor_str = f"R$ {maior}"

        resultado.append({
            "lote":        num,
            "arrematante": nome_limpo or "—",
            "valor":       valor_str
        })

    resultado.sort(key=lambda x: int(x['lote']) if x['lote'].isdigit() else 9999)
    return resultado


@app.route('/upload_extrato', methods=['POST'])
def upload_extrato():
    if 'file' not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400
    f = request.files['file']
    try:
        text = extract_text(f)
        dados = parse_extrato(text)
        if not dados:
            return jsonify({"error": "Nenhum lote encontrado. Verifique se é um Extrato de Leilão válido."}), 400
        return jsonify(dados)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True, use_reloader=False)
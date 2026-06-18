import os
import re
import io
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# ─── EXTRAÇÃO DE TEXTO ───────────────────────────────────────────────────────

def extract_text(file_storage):
    """Extrai texto de PDF, DOCX ou TXT."""
    name = (file_storage.filename or '').lower()
    data = file_storage.read()

    if name.endswith('.pdf'):
        try:
            import pdfplumber
            pages = []
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                for page in pdf.pages:
                    txt = page.extract_text()
                    if txt:
                        pages.append(txt)
            return '\n'.join(pages)
        except Exception as e:
            return ''

    elif name.endswith('.docx'):
        from docx import Document
        doc = Document(io.BytesIO(data))
        return '\n'.join(p.text for p in doc.paragraphs)

    else:
        return data.decode('utf-8', errors='ignore')


# ─── PARSER: RELAÇÃO DE LOTES ─────────────────────────────────────────────────

def parse_relacao(text):
    """
    Extrai dados da Relação de Lotes.
    Retorna dict: { '1': {preco_minimo, preco_avaliado, tipo, descricao}, ... }
    """
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

        # Descrição limpa
        desc = bloco
        remover = [
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
            r'^\s*/\s*/\s*(?:\n|$)',
        ]
        for pat in remover:
            desc = re.sub(pat, ' ', desc, flags=re.IGNORECASE | re.MULTILINE)

        desc = re.sub(r'\s{2,}', ' ', desc).strip(' ,;:/\n\r-')
        if len(desc) > 350:
            desc = desc[:350] + '…'

        mapa[num] = {
            'preco_minimo':  preco_min,
            'preco_avaliado': preco_av,
            'tipo':          tipo,
            'descricao':     desc or tipo or 'Ver edital completo.'
        }

    return mapa


# ─── PARSER: PROPOSTAS E LANCES ──────────────────────────────────────────────

def parse_lances(text):
    """
    Extrai valores de arrematação do relatório de Propostas e Lances.

    Estrutura real do PDF:
        Arrematante: licitante4
        Valor de Arrematação
        Lote: 1
        41.000,00          <-- valor logo após o número do lote
        Estado do Lote: Encerrado

        Arrematante:
        Valor de Arrematação
        Lote: 5
        -                  <-- traço = não arrematado
        Estado do Lote: Lote Não Arrematado

    Retorna dict: { '1': 'R$ 41.000,00', '5': 'Não arrematado', ... }
    """
    mapa = {}

    # Captura "Lote: X" seguido imediatamente de valor ou "-"
    matches = re.findall(
        r'Lote\s*:\s*(\d+)\s*[\r\n]+\s*([\d\.]+,\d{2}|-)',
        text
    )

    for num, valor in matches:
        key = str(int(num))
        mapa[key] = 'Não arrematado' if valor.strip() == '-' else f'R$ {valor}'

    # Fallback: qualquer lote marcado explicitamente como "Não Arrematado"
    nao_arr = re.findall(
        r'Lote\s*:\s*(\d+)[\s\S]{0,150}?Lote\s+Não\s+Arrematado',
        text, re.IGNORECASE
    )
    for num in nao_arr:
        key = str(int(num))
        if key not in mapa:
            mapa[key] = 'Não arrematado'

    return mapa


# ─── ROTAS ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/extrair_pdf', methods=['POST'])
def extrair_pdf():
    """Extrai e retorna o texto de um PDF."""
    if 'file' not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400
    f = request.files['file']
    if not f.filename.lower().endswith('.pdf'):
        return jsonify({"error": "Envie um arquivo PDF (.pdf)"}), 400

    text = extract_text(f)
    if not text.strip():
        return jsonify({"error": "Não foi possível extrair texto deste PDF."}), 500

    linhas = text.count('\n') + 1
    return jsonify({"texto": text, "linhas": linhas})


@app.route('/upload', methods=['POST'])
def upload_edital():
    """Analisa um edital atual (Relação de Lotes)."""
    if 'file' not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400
    f = request.files['file']

    try:
        text = extract_text(f)
        if not text.strip():
            return jsonify({"error": "Não foi possível ler o arquivo."}), 400

        mapa = parse_relacao(text)
        if not mapa:
            return jsonify({"error": "Nenhum lote encontrado. Verifique se o arquivo é uma Relação de Lotes."}), 400

        resultado = [
            {
                "lote":          num,
                "tipo":          d['tipo'],
                "preco_minimo":  d['preco_minimo'],
                "preco_avaliado": d['preco_avaliado'],
                "descricao":     d['descricao'],
            }
            for num, d in mapa.items()
        ]
        resultado.sort(key=lambda x: int(x['lote']) if x['lote'].isdigit() else 999)
        return jsonify(resultado)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/upload_historico', methods=['POST'])
def upload_historico():
    """
    Cruza Relação de Lotes com Propostas e Lances.
    Retorna cada lote com: lote, tipo, preço mínimo, avaliado, valor arrematado, descrição.
    """
    if 'file_relacao' not in request.files or 'file_lances' not in request.files:
        return jsonify({"error": "Envie os dois arquivos: Relação de Lotes e Propostas/Lances."}), 400

    f_relacao = request.files['file_relacao']
    f_lances  = request.files['file_lances']

    try:
        txt_relacao = extract_text(f_relacao)
        txt_lances  = extract_text(f_lances)

        if not txt_relacao.strip():
            return jsonify({"error": "Não foi possível ler o arquivo de Relação de Lotes."}), 400
        if not txt_lances.strip():
            return jsonify({"error": "Não foi possível ler o arquivo de Propostas/Lances."}), 400

        mapa_relacao = parse_relacao(txt_relacao)
        mapa_lances  = parse_lances(txt_lances)

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
    app.run(host='0.0.0.0', port=port, debug=False)
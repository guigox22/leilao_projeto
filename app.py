import os
import re
from flask import Flask, render_template, request, jsonify
from docx import Document

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

# Função padrão para extrair os lotes e dados do arquivo de Relação de Lotes
def extrair_lotes_da_relacao(texto):
    padrao_divisao = r'(?=Lote\s*(?:N[°º\.o]*)?\s*:?\s*\d+)'
    blocos = re.split(padrao_divisao, texto, flags=re.IGNORECASE)
    lotes_mapeados = {}

    for bloco in blocos:
        if not bloco.strip() or "lote" not in bloco.lower():
            continue

        # Captura o número do lote limpando espaços
        match_lote = re.search(r'Lote\s*(?:N[°º\.o]*)?\s*:?\s*(\d+)', bloco, re.IGNORECASE)
        if not match_lote:
            continue
        num_lote = match_lote.group(1).strip()

        padrao_moeda = r'\d{1,3}(?:\.\d{3})*,\d{2}'
        todos_valores = re.findall(padrao_moeda, bloco)

        preco_minimo = "Não encontrado"
        preco_avaliado = "Não encontrado"
        valor_av_bruto = None

        match_av = re.search(r'Avaliado[\s\S]{0,30}?(\d{1,3}(?:\.\d{3})*,\d{2})', bloco, re.IGNORECASE)
        if match_av:
            valor_av_bruto = match_av.group(1)
            preco_avaliado = f"R$ {valor_av_bruto}"

        match_min_direto = re.search(r'Mínimo[\s\S]{0,20}?(\d{1,3}(?:\.\d{3})*,\d{2})', bloco, re.IGNORECASE)
        if match_min_direto:
            preco_minimo = f"R$ {match_min_direto.group(1)}"
        elif todos_valores:
            valores_filtrados = [v for v in todos_valores if v != valor_av_bruto]
            if valores_filtrados:
                preco_minimo = f"R$ {valores_filtrados[-1]}"

        # Limpeza da descrição
        desc = re.sub(r'Lote\s*(?:N[°º\.o]*)?\s*:?\s*\d+', '', bloco, flags=re.IGNORECASE)
        desc = re.sub(r'Preço Mínimo\(R\$\):?', '', desc, flags=re.IGNORECASE)
        desc = re.sub(r'Avaliado em\(R\$\):?', '', desc, flags=re.IGNORECASE)
        desc = re.sub(r'Tipo de Lote:\s*[\w/ÁÉÍÓÚÂÊÎÔÛÃÕÇáéíóúâêîôûãõç]+', '', desc, flags=re.IGNORECASE)
        for v in todos_valores:
            desc = desc.replace(v, '')
        desc = desc.replace('"', ' ').replace('/ /', ' ').replace('|', ' ')
        desc = re.sub(r'\s+', ' ', desc).strip(' ,;:-')

        if not desc or desc.lower() == 'un':
            desc = "Ver edital completo."

        lotes_mapeados[num_lote] = {
            "preco_minimo": preco_minimo,
            "preco_avaliado": preco_avaliado,
            "descricao": desc
        }
    return lotes_mapeados

# --- ROTA 1: PROCESSAMENTO DO EDITAL ATUAL (MANTIDA) ---
@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "Arquivo sem nome"}), 400

    nome_arquivo = file.filename.lower()
    texto_completo = ""

    try:
        if nome_arquivo.endswith('.txt'):
            texto_completo = file.read().decode('utf-8', errors='ignore')
        elif nome_arquivo.endswith('.docx'):
            doc = Document(file)
            linhas_texto = []
            for p in doc.paragraphs:
                if p.text.strip(): linhas_texto.append(p.text)
            for t in doc.tables:
                for r in t.rows:
                    texto_linha = " ".join(c.text.strip() for c in r.cells if c.text.strip())
                    if texto_linha: linhas_texto.append(texto_linha)
            texto_completo = "\n".join(linhas_texto)
        else:
            return jsonify({"error": "Formato inválido."}), 400

        mapa_lotes = extrair_lotes_da_relacao(texto_completo)
        resultado = [{"lote": k, **v} for k, v in mapa_lotes.items()]
        resultado.sort(key=lambda x: int(x['lote']) if x['lote'].isdigit() else 999)
        return jsonify(resultado)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# --- ROTA 2: NOVO CRUZADOR REVERSO (SUA SUGESTÃO) ---
@app.route('/upload_historico', methods=['POST'])
def upload_historico():
    if 'file_relacao' not in request.files or 'file_lances' not in request.files:
        return jsonify({"error": "Selecione os DOIS arquivos requeridos."}), 400
        
    f_relacao = request.files['file_relacao']
    f_lances = request.files['file_lances']
    
    try:
        txt_relacao = f_relacao.read().decode('utf-8', errors='ignore')
        txt_lances = f_lances.read().decode('utf-8', errors='ignore')
        
        # 1. Puxa todas as informações base da Relação de Lotes (Lote, Mínimo, Avaliado, Descrição)
        mapa_relacao = extrair_lotes_da_relacao(txt_relacao)
        
        # 2. Divide o arquivo de Lances em blocos baseados em "Lote : X" ou "Lote X" para não misturar valores
        blocos_lances = re.split(r'(?=Lote\s*(?:N[°º\.o]*)?\s*:?\s*\d+)', txt_lances, flags=re.IGNORECASE)
        
        mapa_arrematacoes = {}
        for bloco in blocos_lances:
            if not bloco.strip() or "lote" not in bloco.lower():
                continue
            
            # Encontra qual é o lote deste bloco de lances
            match_num = re.search(r'Lote\s*(?:N[°º\.o]*)?\s*:?\s*(\d+)', bloco, re.IGNORECASE)
            if not match_num:
                continue
            num_lote_lance = match_num.group(1).strip()
            
            # Pega o valor de Arrematação desse bloco específico
            match_valor = re.search(r'Valor\s+de\s+Arrematação[\s\S]{0,40}?(\d{1,3}(?:\.\d{3})*,\d{2})', bloco, re.IGNORECASE)
            
            if match_valor:
                mapa_arrematacoes[num_lote_lance] = f"R$ {match_valor.group(1)}"
            else:
                mapa_arrematacoes[num_lote_lance] = "Não arrematado"

        # 3. SINCRONIZAÇÃO: Percorre a Relação de Lotes e injeta o valor de arrematação encontrado
        historico_consolidado = []
        
        for num_lote, dados in mapa_relacao.items():
            # Tenta buscar a arrematação correspondente ao número do lote
            valor_final = mapa_arrematacoes.get(num_lote, "Não encontrado")
            
            # Se não achou com o número puro, tenta tirando zeros à esquerda (ex: "01" vira "1")
            if valor_final == "Não encontrado":
                lote_limpo = str(int(num_lote)) if num_lote.isdigit() else num_lote
                valor_final = mapa_arrematacoes.get(lote_limpo, "Não encontrado")

            historico_consolidado.append({
                "lote": num_lote,
                "valor_arrematado": valor_final,
                "preco_minimo": dados["preco_minimo"],
                "preco_avaliado": dados["preco_avaliado"],
                "descricao": dados["descricao"]
            })
            
        # Ordena o resultado final pelo número do lote de forma correta
        historico_consolidado.sort(key=lambda x: int(x['lote']) if x['lote'].isdigit() else 999)
        return jsonify(historico_consolidado)
        
    except Exception as e:
        return jsonify({"error": f"Erro no cruzamento: {str(e)}"}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
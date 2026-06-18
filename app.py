import os
import re
from flask import Flask, render_template, request, jsonify
from docx import Document

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

# --- ROTA 1: PROCESSAMENTO DO EDITAL ATUAL (MANTIDA) ---
@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "Arquivo sem nome"}), 400

    try:
        texto_completo = file.read().decode('utf-8', errors='ignore')
        padrao_divisao = r'(?=Lote\s*(?:N[°º\.o]*)?\s*:?\s*\d+)'
        blocos = re.split(padrao_divisao, texto_completo, flags=re.IGNORECASE)
        resultado = []

        for bloco in blocos:
            if not bloco.strip() or "lote" not in bloco.lower():
                continue
            match_lote = re.search(r'Lote\s*(?:N[°º\.o]*)?\s*:?\s*\d+', bloco, re.IGNORECASE)
            if not match_lote:
                continue
            num_lote = re.search(r'\d+', match_lote.group(0)).group(0)

            padrao_moeda = r'\d{1,3}(?:\.\d{3})*,\d{2}'
            todos_valores = re.findall(padrao_moeda, bloco)
            preco_minimo = "Não encontrado"
            preco_avaliado = "Não encontrado"

            match_av = re.search(r'Avaliado[\s\S]{0,30}?(\d{1,3}(?:\.\d{3})*,\d{2})', bloco, re.IGNORECASE)
            if match_av:
                preco_avaliado = f"R$ {match_av.group(1)}"

            match_min_direto = re.search(r'Mínimo[\s\S]{0,20}?(\d{1,3}(?:\.\d{3})*,\d{2})', bloco, re.IGNORECASE)
            if match_min_direto:
                preco_minimo = f"R$ {match_min_direto.group(1)}"
            elif todos_valores:
                valores_filtrados = [v for v in todos_valores if v != (match_av.group(1) if match_av else "")]
                if valores_filtrados:
                    preco_minimo = f"R$ {valores_filtrados[-1]}"

            desc = re.sub(r'Lote\s*(?:N[°º\.o]*)?\s*:?\s*\d+', '', bloco, flags=re.IGNORECASE)
            desc = re.sub(r'Preço Mínimo\(R\$\):?', '', desc, flags=re.IGNORECASE)
            desc = re.sub(r'Avaliado em\(R\$\):?', '', desc, flags=re.IGNORECASE)
            for v in todos_valores:
                desc = desc.replace(v, '')
            desc = re.sub(r'\s+', ' ', desc).strip(' ,;:-"')

            resultado.append({
                "lote": num_lote,
                "preco_minimo": preco_minimo,
                "preco_avaliado": preco_avaliado,
                "descricao": desc if desc else "Ver edital."
            })

        resultado.sort(key=lambda x: int(x['lote']) if x['lote'].isdigit() else 999)
        return jsonify(resultado)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# --- ROTA 2: PROCESSADOR HISTÓRICO CORRIGIDO (CRUZAMENTO SEGURO POR NÚMERO DE LOTE) ---
@app.route('/upload_historico', methods=['POST'])
def upload_historico():
    if 'file_relacao' not in request.files or 'file_lances' not in request.files:
        return jsonify({"error": "Selecione os DOIS arquivos requeridos."}), 400
        
    f_relacao = request.files['file_relacao']
    f_lances = request.files['file_lances']
    
    try:
        txt_relacao = f_relacao.read().decode('utf-8', errors='ignore')
        txt_lances = f_lances.read().decode('utf-8', errors='ignore')
        
        # 1. MAPEAMENTO EXATO DE ARREMATAÇÃO (Propostas_Lances_por_Lote)
        # Quebramos por "Lote:" para criar blocos individuais limpos de cada lote
        blocos_lances = re.split(r'(?=Lote\s*:\s*\d+)', txt_lances, flags=re.IGNORECASE)
        mapa_arrematacoes = {}

        for bloco in blocos_lances:
            if "lote" not in bloco.lower():
                continue
            
            # Pega o número do lote de forma limpa e isolada
            match_lote = re.search(r'Lote\s*:\s*(\d+)', bloco, re.IGNORECASE)
            if not match_lote:
                continue
            num_lote = str(int(match_lote.group(1).strip())) # Remove zeros à esquerda para garantir casamento perfeito

            # Busca o valor de arrematação dentro deste bloco específico
            match_valor = re.search(r'Valor\s+de\s+Arrematação\s*[\:]?\s*(\d{1,3}(?:\.\d{3})*,\d{2})', bloco, re.IGNORECASE)
            if match_valor:
                mapa_arrematacoes[num_lote] = f"R$ {match_valor.group(1).strip()}"
            else:
                mapa_arrematacoes[num_lote] = "Não arrematado"

        # 2. MAPEAMENTO DA RELAÇÃO DE LOTES
        # Quebramos por "Lote N°:" que é o cabeçalho oficial do arquivo de descrição
        blocos_relacao = re.split(r'(?=Lote\s*N[°º\.o]*\s*:\s*\d+)', txt_relacao, flags=re.IGNORECASE)
        mapa_relacao = {}

        for bloco in blocos_relacao:
            if "lote" not in bloco.lower():
                continue
            
            match_lote = re.search(r'Lote\s*N[°º\.o]*\s*:\s*(\d+)', bloco, re.IGNORECASE)
            if not match_lote:
                continue
            num_lote = str(int(match_lote.group(1).strip()))

            padrao_moeda = r'\d{1,3}(?:\.\d{3})*,\d{2}'
            todos_valores = re.findall(padrao_moeda, bloco)
            
            preco_minimo = "Não encontrado"
            preco_avaliado = "Não encontrado"

            match_av = re.search(r'Avaliado[\s\S]{0,30}?(\d{1,3}(?:\.\d{3})*,\d{2})', bloco, re.IGNORECASE)
            if match_av:
                preco_avaliado = f"R$ {match_av.group(1)}"

            match_min_direto = re.search(r'Mínimo[\s\S]{0,20}?(\d{1,3}(?:\.\d{3})*,\d{2})', bloco, re.IGNORECASE)
            if match_min_direto:
                preco_minimo = f"R$ {match_min_direto.group(1)}"
            elif todos_valores:
                valores_filtrados = [v for v in todos_valores if v != (match_av.group(1) if match_av else "")]
                if valores_filtrados:
                    preco_minimo = f"R$ {valores_filtrados[-1]}"

            # Limpeza cirúrgica da descrição dos itens do lote
            desc = re.sub(r'Lote\s*N[°º\.o]*\s*:\s*\d+', '', bloco, flags=re.IGNORECASE)
            desc = re.sub(r'Preço Mínimo\(R\$\):?', '', desc, flags=re.IGNORECASE)
            desc = re.sub(r'Avaliado em\s*\(R\$\):?', '', desc, flags=re.IGNORECASE)
            desc = re.sub(r'Tipo de Lote:\s*[\w/ÁÉÍÓÚÂÊÎÔÛÃÕÇáéíóúâêîôûãõç\s,]+', '', desc, flags=re.IGNORECASE)
            for v in todos_valores:
                desc = desc.replace(v, '')
            desc = desc.replace('"', ' ').replace('|', ' ')
            desc = re.sub(r'\s+', ' ', desc).strip(' ,;:-"')

            mapa_relacao[num_lote] = {
                "preco_minimo": preco_minimo,
                "preco_avaliado": preco_avaliado,
                "descricao": desc if desc else "Ver edital completo."
            }

        # 3. CRUZAMENTO PURO SOLICITADO
        historico_consolidado = []
        
        # Iteramos com base em todos os lotes identificados na relação
        for num_lote, dados in mapa_relacao.items():
            # Buscamos a arrematação exata mapeada para este número de lote
            valor_final = mapa_arrematacoes.get(num_lote, "Não encontrado")

            historico_consolidado.append({
                "lote": num_lote,
                "valor_arrematado": valor_final,
                "preco_minimo": dados["preco_minimo"],
                "preco_avaliado": dados["preco_avaliado"],
                "descricao": dados["descricao"]
            })
            
        # Ordena a tabela em ordem numérica para exibição limpa
        historico_consolidado.sort(key=lambda x: int(x['lote']) if x['lote'].isdigit() else 999)
        return jsonify(historico_consolidado)
        
    except Exception as e:
        return jsonify({"error": f"Erro no cruzamento: {str(e)}"}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
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


# --- ROTA 2: PROCESSADOR HISTÓRICO ULTRA BLINDADO POR LINHA ---
@app.route('/upload_historico', methods=['POST'])
def upload_historico():
    if 'file_relacao' not in request.files or 'file_lances' not in request.files:
        return jsonify({"error": "Por favor, selecione os DOIS arquivos requeridos."}), 400
        
    f_relacao = request.files['file_relacao']
    f_lances = request.files['file_lances']
    
    try:
        # Forçamos a leitura linha por linha decodificando de forma limpa
        linhas_relacao = f_relacao.read().decode('utf-8', errors='ignore').splitlines()
        linhas_lances = f_lances.read().decode('utf-8', errors='ignore').splitlines()
        
        # 1. MAPEAMENTO DE ARREMATACÕES (Propostas e Lances)
        mapa_arrematacoes = {}
        lote_atual_lance = None
        
        for linha in linhas_lances:
            linha_limpa = linha.strip()
            if not linha_limpa:
                continue
                
            # Identifica a linha do Lote (ex: "Lote: 1")
            match_lote = re.search(r'Lote\s*:\s*(\d+)', linha_limpa, re.IGNORECASE)
            if match_lote:
                lote_atual_lance = str(int(match_lote.group(1))) # "01" vira "1" puro
                continue
                
            # Se já sabemos em qual lote estamos, procuramos o "Valor de Arrematação" nas linhas seguintes
            if lote_atual_lance and "arremat" in linha_limpa.lower():
                match_valor = re.search(r'\d{1,3}(?:\.\d{3})*,\d{2}', linha_limpa)
                if match_valor:
                    mapa_arrematacoes[lote_atual_lance] = f"R$ {match_valor.group(0)}"
                    lote_atual_lance = None # Reseta para procurar o próximo lote

        # 2. MAPEAMENTO DA RELAÇÃO DE LOTES (Descrição e Preços)
        # Como o arquivo de Relação vem em blocos bem definidos por "Lote N°:", unimos o texto para recortar
        texto_relacao_completo = "\n".join(linhas_relacao)
        blocos_relacao = re.split(r'(?=Lote\s*N[°º\.o]*\s*:\s*\d+)', texto_relacao_completo, flags=re.IGNORECASE)
        
        mapa_relacao = {}
        for bloco in blocos_relacao:
            if "lote" not in bloco.lower():
                continue
                
            match_lote_rel = re.search(r'Lote\s*N[°º\.o]*\s*:\s*(\d+)', bloco, re.IGNORECASE)
            if not match_lote_rel:
                continue
            num_lote_rel = str(int(match_lote_rel.group(1).strip())) # "01" vira "1" puro para sincronizar

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

            # Limpeza geral da descrição
            desc = re.sub(r'Lote\s*N[°º\.o]*\s*:\s*\d+', '', bloco, flags=re.IGNORECASE)
            desc = re.sub(r'Preço Mínimo\(R\$\):?', '', desc, flags=re.IGNORECASE)
            desc = re.sub(r'Avaliado em\s*\(R\$\):?', '', desc, flags=re.IGNORECASE)
            desc = re.sub(r'Tipo de Lote:\s*[\w/ÁÉÍÓÚÂÊÎÔÛÃÕÇáéíóúâêîôûãõç\s,]+', '', desc, flags=re.IGNORECASE)
            for v in todos_valores:
                desc = desc.replace(v, '')
            desc = desc.replace('"', ' ').replace('|', ' ')
            desc = re.sub(r'\s+', ' ', desc).strip(' ,;:-"')

            mapa_relacao[num_lote_rel] = {
                "preco_minimo": preco_minimo,
                "preco_avaliado": preco_avaliado,
                "descricao": desc if desc else "Ver edital completo."
            }

        # ----------------------------------------------------
        # 3. O CRUZAMENTO PURO (SINCRONIZADO SEM CARACTERES OCULTOS)
        # ----------------------------------------------------
        historico_consolidado = []
        
        # Percorremos usando as chaves limpas da relação de lotes
        for num_lote, dados in mapa_relacao.items():
            # Puxa o valor do mapa de lances usando a chave perfeitamente igual ("1" com "1")
            valor_arrematado = mapa_arrematacoes.get(num_lote, "Não arrematado")

            historico_consolidado.append({
                "lote": num_lote,
                "valor_arrematado": valor_arrematado,
                "preco_minimo": dados["preco_minimo"],
                "preco_avaliado": dados["preco_avaliado"],
                "descricao": dados["descricao"]
            })
            
        # Garante a ordenação numérica correta
        historico_consolidado.sort(key=lambda x: int(x['lote']) if x['lote'].isdigit() else 999)
        return jsonify(historico_consolidado)
        
    except Exception as e:
        return jsonify({"error": f"Erro interno no cruzamento: {str(e)}"}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
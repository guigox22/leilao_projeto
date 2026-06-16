import os
import re
from flask import Flask, render_template, request, jsonify
from docx import Document

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "Arquivo sem nome válido"}), 400

    try:
        doc = Document(file)
        linhas_texto = []
        
        # 1. Extrai parágrafos estruturados
        for paragrafo in doc.paragraphs:
            if paragrafo.text.strip():
                linhas_texto.append(paragrafo.text)

        # 2. Extrai tabelas mantendo o alinhamento da linha
        for tabela in doc.tables:
            for linha in tabela.rows:
                texto_linha = " ".join(c.text.strip() for c in linha.cells if c.text.strip())
                if texto_linha:
                    linhas_texto.append(texto_linha)

        texto_completo = "\n".join(linhas_texto)

        # 3. Divide o edital pelos Lotes (Garante todas as escritas de 'Lote N°:')
        padrao_divisao = r'(?=Lote\s*(?:N[°º\.o]*)?\s*:?\s*\d+)'
        blocos_lotes = re.split(padrao_divisao, texto_completo, flags=re.IGNORECASE)

        lotes_processados = []

        for bloco in blocos_lotes:
            if not bloco.strip() or "lote" not in bloco.lower():
                continue

            # Captura o número do Lote
            match_lote = re.search(r'Lote\s*(?:N[°º\.o]*)?\s*:?\s*(\d+|S/N)', bloco, re.IGNORECASE)
            num_lote = match_lote.group(1) if match_lote else "S/N"

            # Padrão para achar qualquer valor monetário (ex: 480,00 ou 1.950,00)
            padrao_moeda = r'\d{1,3}(?:\.\d{3})*,\d{2}'
            todos_valores = re.findall(padrao_moeda, bloco)

            preco_minimo = "Não encontrado"
            preco_avaliado = "Não encontrado"

            # --- 1. CAPTURA DO PREÇO AVALIADO ---
            # O Valor Avaliado sempre vem logo após a palavra 'Avaliado em'
            match_av = re.search(r'Avaliado[\s\S]{0,30}?(\d{1,3}(?:\.\d{3})*,\d{2})', bloco, re.IGNORECASE)
            if match_av:
                preco_avaliado = f"R$ {match_av.group(1)}"
                valor_av_bruto = match_av.group(1)
            else:
                valor_av_bruto = None

            # --- 2. CAPTURA DO PREÇO MÍNIMO (ESTRATÉGIA DUPLA) ---
            # Tentativa A: O preço mínimo está logo após a palavra 'Mínimo'
            match_min_direto = re.search(r'Mínimo[\s\S]{0,15}?(\d{1,3}(?:\.\d{3})*,\d{2})', bloco, re.IGNORECASE)
            
            if match_min_direto:
                preco_minimo = f"R$ {match_min_direto.group(1)}"
            elif todos_valores:
                # Tentativa B (Para o Lote 1): O preço mínimo foi jogado para o fim do bloco.
                # Pegamos o último valor monetário do bloco, desde que não seja igual ao valor avaliado.
                valores_filtrados = [v for v in todos_valores if v != valor_av_bruto]
                if valores_filtrados:
                    preco_minimo = f"R$ {valores_filtrados[-1]}"

            # --- 3. LIMPEZA DA DESCRIÇÃO ---
            desc = re.sub(r'Lote\s*(?:N[°º\.o]*)?\s*:?\s*\d+', '', bloco, flags=re.IGNORECASE)
            desc = re.sub(r'Preço Mínimo\(R\$\):?', '', desc, flags=re.IGNORECASE)
            desc = re.sub(r'Avaliado em\(R\$\):?', '', desc, flags=re.IGNORECASE)
            desc = re.sub(r'Tipo de Lote:\s*[\w/ÁÉÍÓÚÂÊÎÔÛÃÕÇáéíóúâêîôûãõç]+', '', desc, flags=re.IGNORECASE)
            
            # Remove os valores financeiros da descrição para deixá-lo limpo
            for v in todos_valores:
                desc = desc.replace(v, '')
            
            desc = desc.replace('"', ' ').replace('/ /', ' ').replace('|', ' ')
            desc = re.sub(r'\s+', ' ', desc).strip(' ,;:-')

            if not desc or desc.lower() == 'un':
                desc = "Ver detalhes no edital."

            lotes_processados.append({
                "lote": num_lote,
                "preco_minimo": preco_minimo,
                "preco_avaliado": preco_avaliado,
                "descricao": desc
            })

        if not lotes_processados:
            return jsonify([{"lote": "Aviso", "preco_minimo": "-", "descricao": "Layout incompatível."}])

        return jsonify(lotes_processados)

    except Exception as e:
        return jsonify({"error": f"Erro no processamento: {str(e)}"}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
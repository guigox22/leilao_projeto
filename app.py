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
        
        # 1. Extrai parágrafos normais
        for paragrafo in doc.paragraphs:
            if paragrafo.text.strip():
                linhas_texto.append(paragrafo.text)

        # 2. Extrai tabelas mantendo o texto corrido por linha
        for tabela in doc.tables:
            for linha in tabela.rows:
                texto_linha = " ".join(c.text.strip() for c in linha.cells if c.text.strip())
                if texto_linha:
                    linhas_texto.append(texto_linha)

        texto_completo = "\n".join(linhas_texto)

        # 3. Quebra por Lotes
        padrao_divisao = r'(?=Lote\s*(?:N[°º\.o]*)?\s*:?\s*\d+)'
        blocos_lotes = re.split(padrao_divisao, texto_completo, flags=re.IGNORECASE)

        lotes_processados = []

        for bloco in blocos_lotes:
            if not bloco.strip() or "lote" not in bloco.lower():
                continue

            # Captura número do Lote
            match_lote = re.search(r'Lote\s*(?:N[°º\.o]*)?\s*:?\s*(\d+)', bloco, re.IGNORECASE)
            num_lote = match_lote.group(1) if match_lote else "S/N"

            # Captura todos os valores em formato de moeda (ex: 480,00 ou 1.950,00)
            padrao_moeda = r'\d{1,3}(?:\.\d{3})*,\d{2}'
            todos_valores = re.findall(padrao_moeda, bloco)

            preco_minimo = "Não encontrado"
            preco_avaliado = "Não encontrado"
            valor_av_bruto = None

            # --- CAPTURA PREÇO AVALIADO ---
            match_av = re.search(r'Avaliado[\s\S]{0,30}?(\d{1,3}(?:\.\d{3})*,\d{2})', bloco, re.IGNORECASE)
            if match_av:
                valor_av_bruto = match_av.group(1)
                preco_avaliado = f"R$ {valor_av_bruto}"

            # --- CAPTURA PREÇO MÍNIMO ---
            # Se a palavra Mínimo tiver o valor grudado (como no Lote 2)
            match_min_direto = re.search(r'Mínimo[\s\S]{0,15}?(\d{1,3}(?:\.\d{3})*,\d{2})', bloco, re.IGNORECASE)
            if match_min_direto:
                preco_minimo = f"R$ {match_min_direto.group(1)}"
            elif todos_valores:
                # Se o valor foi jogado pro fim da linha (como no Lote 1)
                valores_filtrados = [v for v in todos_valores if v != valor_av_bruto]
                if valores_filtrados:
                    preco_minimo = f"R$ {valores_filtrados[-1]}"

            # --- LIMPEZA DA DESCRIÇÃO ---
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

            # AGORA O PYTHON ENVIA ESSES 4 NOMES EXATOS:
            lotes_processados.append({
                "lote": num_lote,
                "preco_minimo": preco_minimo,
                "preco_avaliado": preco_avaliado,
                "descricao": desc
            })

        return jsonify(lotes_processados)

    except Exception as e:
        return jsonify({"error": f"Erro no processamento: {str(e)}"}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
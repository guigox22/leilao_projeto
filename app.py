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
        
        # 1. Lê parágrafos soltos
        for paragrafo in doc.paragraphs:
            if paragrafo.text.strip():
                linhas_texto.append(paragrafo.text)

        # 2. Lê tabelas misturadas
        for tabela in doc.tables:
            for linha in tabela.rows:
                texto_linha = " ".join(c.text.strip() for c in linha.cells if c.text.strip())
                if texto_linha:
                    linhas_texto.append(texto_linha)

        texto_completo = "\n".join(linhas_texto)

        # 3. Quebra o texto por lotes
        padrao_divisao = r'(?=Lote\s*(?:N[°º\.o]*)?\s*:?\s*\d+)'
        blocos_lotes = re.split(padrao_divisao, texto_completo, flags=re.IGNORECASE)

        lotes_processados = []

        for bloco in blocos_lotes:
            if not bloco.strip() or "lote" not in bloco.lower():
                continue

            # Captura o Lote
            match_lote = re.search(r'Lote\s*(?:N[°º\.o]*)?\s*:?\s*(\d+|S/N)', bloco, re.IGNORECASE)
            num_lote = match_lote.group(1) if match_lote else "S/N"

            # PADRÃO MESTRE: Captura tudo que for dinheiro (ex: 1.950,00 ou 480,00 ou 1,00)
            padrao_moeda = r'\d{1,3}(?:\.\d{3})*,\d{2}'
            todos_valores = re.findall(padrao_moeda, bloco)

            preco_avaliado = "Não encontrado"
            preco_minimo = "Não encontrado"
            preco_av_bruto = None

            # --- CAPTURA INTELIGENTE DE PREÇOS ---
            # O Preço Avaliado é o valor que aparece após a palavra "Avaliado"
            partes_av = re.split(r'Avaliado', bloco, flags=re.IGNORECASE)
            if len(partes_av) > 1:
                match_av = re.search(padrao_moeda, partes_av[1])
                if match_av:
                    preco_av_bruto = match_av.group(0)
                    preco_avaliado = f"R$ {preco_av_bruto}"

            # O Preço Mínimo é o maior valor que sobrou na frase inteira
            valores_restantes = [v for v in todos_valores if v != preco_av_bruto]
            
            if valores_restantes:
                try:
                    # Converte para decimal para não confundir com quantidades e achar o maior
                    maior_restante = max(valores_restantes, key=lambda x: float(x.replace('.', '').replace(',', '.')))
                    preco_minimo = f"R$ {maior_restante}"
                except:
                    pass

            # --- LIMPEZA DE DESCRIÇÃO ---
            desc = re.sub(r'Lote\s*(?:N[°º\.o]*)?\s*:?\s*\d+', '', bloco, flags=re.IGNORECASE)
            desc = re.sub(r'Preço Mínimo\(R\$\):?', '', desc, flags=re.IGNORECASE)
            desc = re.sub(r'Avaliado em\(R\$\):?', '', desc, flags=re.IGNORECASE)
            desc = re.sub(r'Tipo de Lote:\s*[\w/ÁÉÍÓÚÂÊÎÔÛÃÕÇáéíóúâêîôûãõç]+', '', desc, flags=re.IGNORECASE)
            
            # Remove os valores numéricos limpos para não poluir a descrição
            for v in todos_valores:
                desc = desc.replace(v, '')
            
            # Remove lixos da tabela
            desc = desc.replace('"', ' ').replace('/ /', ' ').replace('|', ' ')
            desc = re.sub(r'\s+', ' ', desc).strip(' ,;')

            if not desc or desc.lower() == 'un':
                desc = "Produto sem descrição detalhada."

            lotes_processados.append({
                "lote": num_lote,
                "preco_minimo": preco_minimo,
                "preco_avaliado": preco_avaliado,
                "descricao": desc
            })

        if not lotes_processados:
            return jsonify([{"lote": "Aviso", "preco_minimo": "-", "descricao": "O documento foi lido, mas a formatação falhou."}])

        return jsonify(lotes_processados)

    except Exception as e:
        return jsonify({"error": f"Erro no processamento: {str(e)}"}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
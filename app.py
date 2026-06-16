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
        
        # Lê os parágrafos normais
        for paragrafo in doc.paragraphs:
            if paragrafo.text.strip():
                linhas_texto.append(paragrafo.text)

        # Lê os textos das tabelas e transforma em linhas de texto puro
        for tabela in doc.tables:
            for linha in tabela.rows:
                texto_linha = " ".join(c.text.strip() for c in linha.cells if c.text.strip())
                if texto_linha:
                    linhas_texto.append(texto_linha)

        texto_completo = "\n".join(linhas_texto)

        # 1. DIVISÃO BLINDADA: Aceita "Lote N°:", "Lote:", "Lote 1", ignorando espaços e aspas
        padrao_divisao = r'(?=Lote\s*(?:N[°º\.o]*)?\s*:?\s*\d+)'
        blocos_lotes = re.split(padrao_divisao, texto_completo, flags=re.IGNORECASE)

        lotes_processados = []

        for bloco in blocos_lotes:
            if not bloco.strip() or "lote" not in bloco.lower():
                continue

            # Captura o Número do Lote
            match_lote = re.search(r'Lote\s*(?:N[°º\.o]*)?\s*:?\s*(\d+|S/N)', bloco, re.IGNORECASE)
            num_lote = match_lote.group(1) if match_lote else "S/N"

            # 2. CAPTURA DE PREÇOS COM INTELIGÊNCIA ARTIFICIAL (Lógica de Exclusão)
            
            # Pega o Valor Avaliado (é sempre o número próximo da palavra Avaliado)
            match_av = re.search(r'Avaliado[\s\S]{0,40}?([\d]{1,3}(?:\.\d{3})*,\d{2})', bloco, re.IGNORECASE)
            preco_av_bruto = match_av.group(1) if match_av else None
            preco_avaliado = f"R$ {preco_av_bruto}" if preco_av_bruto else "Não encontrado"

            # Tenta achar o Preço Mínimo diretamente (caso esteja formatado direitinho)
            match_min = re.search(r'Mínimo[\s\S]{0,25}?([\d]{1,3}(?:\.\d{3})*,\d{2})', bloco, re.IGNORECASE)
            preco_min_bruto = match_min.group(1) if match_min else None

            # SE O PREÇO MÍNIMO ESTIVER PERDIDO (Como no Lote 1 dos iPhones):
            if not preco_min_bruto:
                # Busca TODOS os valores em Reais no bloco que NÃO são quantidades (un, kg, etc)
                padrao_precos = r'\b(\d{1,3}(?:\.\d{3})*,\d{2})\b(?!\s*(?:un|unid|kg|g|mg|l|ml|m|cm|mm|pc|peças?|pares?|kit|cx)\b)'
                todos_precos = re.findall(padrao_precos, bloco, re.IGNORECASE)
                
                # O preço mínimo será o maior valor monetário que sobrar no bloco tirando a avaliação
                precos_restantes = [p for p in todos_precos if p != preco_av_bruto]
                if precos_restantes:
                    # Converte pra número pra achar o maior com segurança
                    preco_min_bruto = max(precos_restantes, key=lambda x: float(x.replace('.', '').replace(',', '.')))

            preco_minimo = f"R$ {preco_min_bruto}" if preco_min_bruto else "Não encontrado"

            # 3. EXTRAÇÃO DA DESCRIÇÃO (Sem deletar a linha toda)
            # Remove os cabeçalhos diretamente do texto, sobrando apenas o produto
            desc = re.sub(r'Lote\s*(?:N[°º\.o]*)?\s*:?\s*\d+', '', bloco, flags=re.IGNORECASE)
            desc = re.sub(r'Preço Mínimo\(R\$\):?(?:[\s,"]*[\d.,]+)?', '', desc, flags=re.IGNORECASE)
            desc = re.sub(r'Avaliado em\(R\$\):?(?:[\s,"]*[\d.,]+)?', '', desc, flags=re.IGNORECASE)
            desc = re.sub(r'Tipo de Lote:\s*[\w/ÁÉÍÓÚÂÊÎÔÛÃÕÇáéíóúâêîôûãõç]+', '', desc, flags=re.IGNORECASE)
            
            # Limpa caracteres lixo de tabelas (como aspas e barras perdidas)
            desc = desc.replace('"', ' ').replace('/ /', ' ').replace('|', ' ')
            desc = re.sub(r'\s+', ' ', desc).strip(' ,')

            if not desc:
                desc = "Produto sem descrição."

            lotes_processados.append({
                "lote": num_lote,
                "preco_minimo": preco_minimo,
                "preco_avaliado": preco_avaliado,
                "descricao": desc
            })

        if not lotes_processados:
            return jsonify([{"lote": "Aviso", "preco_minimo": "-", "descricao": "Tabela lida, mas o layout Lote N° não conectou."}])

        return jsonify(lotes_processados)

    except Exception as e:
        return jsonify({"error": f"Erro no processamento: {str(e)}"}), 500


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
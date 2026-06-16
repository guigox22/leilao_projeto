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
        for paragrafo in doc.paragraphs:
            if paragrafo.text.strip():
                linhas_texto.append(paragrafo.text)

        for tabela in doc.tables:
            for linha in tabela.rows:
                texto_linha = " ".join(
                    c.text.strip() for c in linha.cells if c.text.strip()
                )
                if texto_linha:
                    linhas_texto.append(texto_linha)

        texto_completo = "\n".join(linhas_texto)

        # Divide o edital em blocos por Lote
        blocos_lotes = re.split(
            r'(?=Lote\s*(?:N°|Nº|:))', texto_completo, flags=re.IGNORECASE
        )

        lotes_processados = []

        for bloco in blocos_lotes:
            if not bloco.strip() or "Lote" not in bloco:
                continue

            # Captura o número do lote
            match_lote = re.search(
                r'Lote\s*(?:N°|Nº|:)?\s*(\d+|S/N)', bloco, re.IGNORECASE
            )
            num_lote = match_lote.group(1) if match_lote else "S/N"

            # Captura de Preço Mínimo e Avaliado imbatível contra espaços
            match_min = re.search(
                r"Preço\s+Mínimo\(R\$\):\s*([\d.,]+)", bloco, re.IGNORECASE
            )
            preco_minimo = (
                f"R$ {match_min.group(1).strip()}"
                if match_min else "Não encontrado"
            )

            match_av = re.search(
                r"Avaliado\s+em\(R\$\):\s*([\d.,]+)", bloco, re.IGNORECASE
            )
            preco_avaliado = (
                f"R$ {match_av.group(1).strip()}"
                if match_av else "Não encontrado"
            )

            # Extrai o que sobrou para preencher a descrição
            linhas_bloco = [l.strip() for l in bloco.split('\n') if l.strip()]
            descricao_linhas = []
            for l in linhas_bloco:
                termo = l.lower()
                if not any(p in termo for p in [
                    "lote nº", "lote n°", "preço mínimo",
                    "avaliado em", "tipo de lote:"
                ]):
                    descricao_linhas.append(l)

            descricao = (
                " ".join(descricao_linhas).strip()
                if descricao_linhas else "Aparelho Tecnológico"
            )
            descricao = re.sub(r'\s+', ' ', descricao)

            lotes_processados.append({
                "lote": num_lote,
                "preco_minimo": preco_minimo,
                "preco_avaliado": preco_avaliado,
                "descricao": descricao
            })

        if not lotes_processados:
            return jsonify({
                "error": "Não foi possível estruturar os lotes."
            }), 400

        return jsonify(lotes_processados)

    except Exception as e:
        return jsonify({"error": f"Erro no processamento: {str(e)}"}), 500


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
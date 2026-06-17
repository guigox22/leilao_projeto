import os
import re
import io
from flask import Flask, render_template, request, jsonify
from docx import Document
import pdfplumber

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

    nome_arquivo = file.filename.lower()
    texto_completo = ""

    try:
        # --- SE O USUÁRIO ENVIAR UM PDF ---
        if nome_arquivo.endswith('.pdf'):
            # 1. Cria um arquivo Word virtual na memória do servidor
            doc_virtual = Document()
            
            # 2. Abre o PDF e extrai o texto de forma bruta, página por página
            with pdfplumber.open(file) as pdf:
                for pagina in pdf.pages:
                    texto_pag = pagina.extract_text()
                    if texto_pag:
                        # 3. Escreve o texto extraído dentro do nosso Word virtual
                        doc_virtual.add_paragraph(texto_pag)
            
            # 4. Salva o Word virtual em um buffer de memória
            word_em_memoria = io.BytesIO()
            doc_virtual.save(word_em_memoria)
            word_em_memoria.seek(0)
            
            # 5. Aponta o arquivo a ser lido para este novo Word que acabamos de criar!
            arquivo_para_ler = Document(word_em_memoria)

        # --- SE O USUÁRIO JÁ ENVIAR UM WORD DIRETO ---
        elif nome_arquivo.endswith('.docx'):
            arquivo_para_ler = Document(file)
        
        else:
            return jsonify({"error": "Formato não suportado. Envie .docx ou .pdf"}), 400


        # --- DAQUI PRA FRENTE É O PROCESSO PADRÃO DO WORD QUE JÁ FUNCIONA ---
        linhas_texto = []
        
        # Lê os parágrafos do Word (seja o enviado ou o convertido do PDF)
        for paragrafo in arquivo_para_ler.paragraphs:
            if paragrafo.text.strip():
                linhas_texto.append(paragrafo.text)
                
        # Lê as tabelas do Word
        for tabela in arquivo_para_ler.tables:
            for linha in tabela.rows:
                texto_linha = " ".join(c.text.strip() for c in linha.cells if c.text.strip())
                if texto_linha:
                    linhas_texto.append(texto_linha)

        texto_completo = "\n".join(linhas_texto)

        # Divisão inteligente por lotes
        padrao_divisao = r'(?=Lote\s*(?:N[°º\.o]*)?\s*:?\s*\d+)'
        blocos_lotes = re.split(padrao_divisao, texto_completo, flags=re.IGNORECASE)

        lotes_processados = []

        for bloco in blocos_lotes:
            if not bloco.strip() or "lote" not in bloco.lower():
                continue

            # Captura número do Lote
            match_lote = re.search(r'Lote\s*(?:N[°º\.o]*)?\s*:?\s*\d+', bloco, re.IGNORECASE)
            if match_lote:
                num_lote = re.search(r'\d+', match_lote.group(0))
                num_lote = num_lote.group(0) if num_lote else "S/N"
            else:
                num_lote = "S/N"

            # Captura valores em formato de moeda
            padrao_moeda = r'\d{1,3}(?:\.\d{3})*,\d{2}'
            todos_valores = re.findall(padrao_moeda, bloco)

            preco_minimo = "Não encontrado"
            preco_avaliado = "Não encontrado"
            valor_av_bruto = None

            # Captura preço Avaliado
            match_av = re.search(r'Avaliado[\s\S]{0,30}?(\d{1,3}(?:\.\d{3})*,\d{2})', bloco, re.IGNORECASE)
            if match_av:
                valor_av_bruto = match_av.group(1)
                preco_avaliado = f"R$ {valor_av_bruto}"

            # Captura preço Mínimo (Estratégia dupla padrão)
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

            lotes_processados.append({
                "lote": num_lote,
                "preco_minimo": preco_minimo,
                "preco_avaliado": preco_avaliado,
                "descricao": desc
            })

        if not lotes_processados:
            return jsonify({"error": "Nenhum lote pôde ser estruturado a partir deste arquivo."}), 400

        return jsonify(lotes_processados)

    except Exception as e:
        return jsonify({"error": f"Erro na conversão/leitura: {str(e)}"}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
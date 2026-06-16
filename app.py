import os
import re
from flask import Flask, render_template, request, jsonify
# Importe aqui a biblioteca que você está usando (ex: import pdfplumber ou de leitura de docx)

app = Flask(__name__)

# Rota principal para carregar a página do painel
@app.route('/')
def index():
    return render_template('index.html')

# Rota que recebe o arquivo do edital e faz a extração
@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "Arquivo sem nome válido"}), 400

    # 1. SIMULAÇÃO DA EXTRAÇÃO DE TEXTO
    # Aqui o seu código lê o arquivo (Word ou PDF) e transforma em texto bruto.
    # Vamos supor que a sua variável de texto bruto se chame 'texto_completo'
    texto_completo = "" 
    
    # [O seu código atual de leitura do arquivo entra aqui para preencher o texto_completo]

    # 2. SEPARAÇÃO DOS LOTES
    # Geralmente quebramos o texto usando a palavra "Lote N°" ou similar para isolar cada bloco
    blocos_lotes = re.split(r'(?=Lote\s+N°:)', texto_completo, flags=re.IGNORECASE)
    
    lotes_processados = []

    for bloco in blocos_lotes:
        if not bloco.strip():
            continue
            
        # --- AQUI ESTÁ A CORREÇÃO CIRÚRGICA DOS PREÇOS ---
        
        # Captura o número do Lote (Ex: Lote N°: 1)
        match_lote = re.search(r'Lote\s+N°:\s*(\d+|S/N)', bloco, re.IGNORECASE)
        num_lote = match_lote.group(1) if match_lote else "S/N"
        
        # Captura o Preço Mínimo isolando o termo correto e pegando o número com pontos/vírgulas
        # O \s* garante que ele ache o valor colado ou cheio de espaços/quebras de linha
        match_minimo = re.search(r"Preço\s+Mínimo\(R\$\):\s*([\d.,]+)", bloco, re.IGNORECASE)
        preco_minimo = f"R$ {match_minimo.group(1).strip()}" if match_minimo else "Não encontrado"
        
        # Captura o Valor Avaliado isolando o termo correto
        match_avaliado = re.search(r"Avaliado\s+em\(R\$\):\s*([\d.,]+)", bloco, re.IGNORECASE)
        preco_avaliado = f"R$ {match_avaliado.group(1).strip()}" if match_avaliado else "Não encontrado"
        
        # Captura a descrição do produto (ajuste a regex conforme a palavra-chave que você usa para o fim do bloco)
        # Exemplo genérico buscando o texto que sobra no bloco:
        match_desc = re.search(r"(?:un|unid|unidade)\s+(.*)", bloco, re.IGNORECASE | re.DOTALL)
        descricao = match_desc.group(1).strip() if match_desc else "Descrição não identificada"
        # Limpa quebras de linha repetidas da descrição para ficar bonito na tabela
        descricao = re.sub(r'\s+', ' ', descricao)

        # Monta o dicionário padronizado do lote
        lotes_processados.append({
            "lote": num_lote,
            "preco_minimo": preco_minimo,
            "preco_avaliado": preco_avaliado,
            "descricao": descricao
        })

    # Retorna o resultado final para o seu JavaScript construir a tabela na tela
    return jsonify(lotes_processados)

# Configuração que descobrimos para o Render rodar sem travar a porta externa
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)